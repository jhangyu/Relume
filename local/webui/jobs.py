import json
import os
import re
import signal
import sqlite3
import subprocess
import threading
import time
import traceback

from . import db

OPTION_DEFAULTS = {
    'upstream': {'pull': True, 'rebuild': False, 'notify': True},
    'build': {'notify': True},
    'ota': {'retries': 0, 'mode': 'normal', 'package': None, 'notify': True},
}
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
_handlers = {}
_running = {}
_conds = {}
_lock = threading.Lock()
_wake = threading.Condition()
_threads = []
_stop = threading.Event()


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(',', ':'))


def normalize(kind, options):
    o = options or {}
    return {k: o.get(k, d) for k, d in OPTION_DEFAULTS[kind].items()}


class JobFailed(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Job:
    def __init__(self, row):
        self.id = row['id']
        self.kind = row['kind']
        self.target = json.loads(row['target'])
        self.options = json.loads(row['options'])
        self.trigger = row['trigger']
        self.schedule_id = row['schedule_id']
        self.cancel_event = threading.Event()


def log_condition(job_id):
    with _lock:
        return _conds.setdefault(job_id, threading.Condition())  # ponytail: never pruned, one small object per job


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def isolate(argv):
    """argv for third-party code (patch bundles, CLIs; spec L779): runs as RV_RUNNER_UID, same gid, no supplementary
    groups, no capabilities, no new privileges. Unset (tests, bare image) = the server's own uid."""
    uid = os.environ.get('RV_RUNNER_UID')
    if not uid:
        return list(argv)
    return ['setpriv', f'--reuid={uid}', f'--regid={os.getgid()}', '--clear-groups',
            '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs', *argv]


def runner_path(p):
    """Path under db.DATA as the isolated runner sees it: compose mounts work, apk-cache and upstream at
    RV_RUNNER_DATA because DATA itself sits in a directory the runner uid cannot enter."""
    root = os.environ.get('RV_RUNNER_DATA')
    return os.path.join(root, os.path.relpath(p, db.DATA)) if root else str(p)


class Log:
    def __init__(self, job):
        self.job = job
        self.f = open(db.DATA / 'logs' / f'{job.id}.log', 'a', encoding='utf-8')
        self.cond = log_condition(job.id)

    def line(self, text):
        stamp = time.strftime('%H:%M:%S ')
        for t in ANSI.sub('', str(text)).splitlines() or ['']:
            self.f.write(stamp + t + '\n')
        self.f.flush()
        with self.cond:
            self.cond.notify_all()

    def run(self, argv, cwd=None, env=None, timeout=None, on_line=None):
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True, text=True, errors='replace')
        done, timed_out = threading.Event(), threading.Event()
        deadline = time.monotonic() + timeout if timeout else None

        def watch():
            while not done.wait(0.2):
                cancel = self.job.cancel_event.is_set()
                if cancel or (deadline and time.monotonic() > deadline):
                    if not cancel:
                        timed_out.set()
                    _kill_group(p)
                    return

        threading.Thread(target=watch, daemon=True).start()
        try:
            for raw in p.stdout:
                s = ANSI.sub('', raw.rstrip('\n'))
                self.line(s)
                if on_line:
                    on_line(s)
        except BaseException:  # a failing on_line must not leave the child running (1 build at a time)
            _kill_group(p)
            raise
        finally:
            p.stdout.close()
            rc = p.wait()
            done.set()
        if timed_out.is_set():
            raise JobFailed('timeout')
        if self.job.cancel_event.is_set():
            raise JobFailed('cancelled')
        return rc

    def close(self):
        self.f.close()
        with self.cond:
            self.cond.notify_all()


def register(kind, handler, detail=None):
    _handlers[kind] = (handler, detail)


def reset():
    _handlers.clear()


def enqueue(kind, target, trigger, schedule_id=None, options=None):
    t, o = canon(target), canon(normalize(kind, options))
    with db.tx() as c:
        r = c.execute("SELECT id FROM jobs WHERE kind=? AND status='queued' AND target=? AND options=?",
                      (kind, t, o)).fetchone()
        if r:
            return r['id']
        jid = c.execute('INSERT INTO jobs(kind,status,trigger,schedule_id,target,options,created_at) '
                        "VALUES (?,'queued',?,?,?,?,?)",
                        (kind, trigger, schedule_id, t, o, db.utcnow())).lastrowid
    with _wake:
        _wake.notify_all()
    return jid


def _execute(job):
    handler = _handlers[job.kind][0]
    log = Log(job)
    with _lock:
        _running[job.id] = job
    status, reason = 'success', None
    try:
        handler(job, log)
    except JobFailed as e:
        status, reason = 'failed', e.reason
    except Exception as e:
        first = (str(e).splitlines() or [''])[0]
        status, reason = 'failed', f'{type(e).__name__}: {first}' if first else type(e).__name__
        log.line(traceback.format_exc())
    finally:
        with _lock:
            _running.pop(job.id, None)
    if job.cancel_event.is_set():
        status, reason = 'cancelled', 'cancelled'
    with db.tx() as c:
        # a handler that returns normally may have written jobs.reason itself (OTA outcome, spec L914)
        c.execute('UPDATE jobs SET status=?, reason=COALESCE(?, reason), finished_at=? WHERE id=?',
                  (status, reason, db.utcnow(), job.id))
    log.close()
    _cleanup(job.kind)
    return status


def _claim(kind):
    with db.tx() as c:
        r = c.execute("SELECT * FROM jobs WHERE kind=? AND status='queued' ORDER BY id LIMIT 1",
                      (kind,)).fetchone()
        if r is None:
            return None
        c.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (db.utcnow(), r['id']))
    return Job(r)


def _worker(kind):
    while not _stop.is_set():
        job = _claim(kind) if kind in _handlers else None
        if job is None:
            with _wake:
                _wake.wait(5)
            continue
        _execute(job)


def start():
    if _threads:
        return
    _stop.clear()
    for kind in OPTION_DEFAULTS:
        t = threading.Thread(target=_worker, args=(kind,), daemon=True, name=f'jobs-{kind}')
        t.start()
        _threads.append(t)


def stop():
    """Tests only: end the worker threads (a running handler finishes first)."""
    _stop.set()
    with _wake:
        _wake.notify_all()
    for t in _threads:
        t.join(15)
    _threads.clear()


def run_inline(kind, target, trigger, options=None):
    with db.tx() as c:
        jid = c.execute('INSERT INTO jobs(kind,status,trigger,target,options,created_at,started_at) '
                        "VALUES (?,'running',?,?,?,?,?)",
                        (kind, trigger, canon(target), canon(normalize(kind, options)),
                         db.utcnow(), db.utcnow())).lastrowid
    _execute(Job(db.conn().execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()))
    return get(jid)


def cancel(job_id):
    with db.tx() as c:
        n = c.execute("UPDATE jobs SET status='cancelled', reason='cancelled', finished_at=? "
                      "WHERE id=? AND status='queued'", (db.utcnow(), job_id)).rowcount
    if n:
        return 'cancelled'
    with _lock:
        job = _running.get(job_id)
    if job:
        job.cancel_event.set()
        return 'cancelling'
    return None


def recover():
    with db.tx() as c:
        c.execute("UPDATE jobs SET status='failed', reason='server restarted', finished_at=? "
                  "WHERE status='running'", (db.utcnow(),))


def _cleanup(kind):
    from . import settings
    keep = int(settings.get('job_history_count', 500))
    old = [r[0] for r in db.conn().execute(
        "SELECT id FROM jobs WHERE kind=? AND status NOT IN ('queued','running') "
        'ORDER BY id DESC LIMIT -1 OFFSET ?', (kind, keep))]
    for jid in old:
        try:
            with db.tx() as c:
                c.execute('DELETE FROM jobs WHERE id=?', (jid,))
        except sqlite3.IntegrityError:
            continue
        (db.DATA / 'logs' / f'{jid}.log').unlink(missing_ok=True)


def to_json(row, with_detail=True):
    d = dict(row)
    d['target'] = json.loads(d['target'])
    d['options'] = json.loads(d['options'])
    detail = _handlers.get(d['kind'], (None, None))[1]
    d['detail'] = detail(d['id']) if with_detail and detail else None
    return d


def get(job_id):
    r = db.conn().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    return to_json(r, with_detail=False) if r else None


KEEPALIVE = 15


def log_stream(job_id, offset=0):
    path = db.DATA / 'logs' / f'{job_id}.log'
    cond = log_condition(job_id)
    last = time.monotonic()
    while True:
        row = db.conn().execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
        done = row is None or row['status'] not in ('queued', 'running')
        chunk = b''
        if path.exists():
            with open(path, 'rb') as f:
                f.seek(offset)
                chunk = f.read()
            if not done:
                chunk = chunk[:chunk.rfind(b'\n') + 1]
        if chunk:
            offset += len(chunk)
            lines = chunk.decode('utf-8', 'replace').rstrip('\n').split('\n')
            yield (f'id: {offset}\n' + ''.join(f'data: {x}\n' for x in lines) + '\n').encode()
            last = time.monotonic()
        if done:
            yield f"event: end\ndata: {row['status'] if row else 'missing'}\n\n".encode()
            return
        with cond:
            cond.wait(1)
        if time.monotonic() - last >= KEEPALIVE:
            yield b': keepalive\n\n'
            last = time.monotonic()


def _int(q, key):
    from .server import HttpError
    try:
        return int(q[key])
    except ValueError:
        raise HttpError(400, f'invalid {key}')


def list_route(req):
    q, where, args = req.query, [], []
    for key in ('kind', 'status'):
        if key in q:
            where.append(f'{key}=?')
            args.append(q[key])
    for key in ('item_id', 'device_id'):
        if key in q:
            where.append(f"json_extract(target,'$.{key}')=?")
            args.append(_int(q, key))
    if 'before_id' in q:
        where.append('id<?')
        args.append(_int(q, 'before_id'))
    limit = max(1, min(_int(q, 'limit'), 100)) if 'limit' in q else 50  # LIMIT <0 = unlimited in SQLite
    sql = 'SELECT * FROM jobs' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY id DESC LIMIT ?'
    return 200, {'jobs': [to_json(r) for r in db.conn().execute(sql, (*args, limit))]}


def _row_or_404(job_id):
    from .server import HttpError
    r = db.conn().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if r is None:
        raise HttpError(404, 'job not found')
    return r


def get_route(req):
    return 200, to_json(_row_or_404(req.params['id']))


def cancel_route(req):
    from .server import HttpError
    _row_or_404(req.params['id'])
    res = cancel(req.params['id'])
    if res is None:
        raise HttpError(409, 'job already finished')
    return 200, {'status': res}


def log_route(req):
    from .server import SSEResponse
    _row_or_404(req.params['id'])
    try:
        offset = max(0, int(req.headers.get('Last-Event-ID') or 0))
    except ValueError:
        offset = 0
    return SSEResponse(log_stream(req.params['id'], offset))


ROUTES = [
    ('GET', '/api/jobs', list_route),
    ('GET', '/api/jobs/{id}', get_route),
    ('POST', '/api/jobs/{id}/cancel', cancel_route),
    ('GET', '/api/jobs/{id}/log', log_route),
]
