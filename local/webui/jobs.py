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
        for raw in p.stdout:
            s = ANSI.sub('', raw.rstrip('\n'))
            self.line(s)
            if on_line:
                on_line(s)
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
