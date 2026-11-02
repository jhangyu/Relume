import contextlib
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
import traceback

from . import db, events, settings
from .server import HttpError, SSEResponse

OPTION_DEFAULTS = {
    'upstream': {'notify': True},
    'build': {'notify': True},
    'ota': {'retries': 0, 'mode': 'normal', 'package': None, 'notify': True},
}
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
_handlers = {}
_running = {}
_conds = {}
_lock = threading.Lock()


_wake = events.Signal()  # enqueue, register, stop: a worker sees a notify sent while it was claiming
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


class JobCancelled(JobFailed):
    """The job's cancel_event fired while it ran or waited."""

    def __init__(self, reason='cancelled'):
        super().__init__(reason)


class JobTimeout(JobFailed):
    """A child process exceeded its timeout (Log.run)."""

    def __init__(self, reason='timeout'):
        super().__init__(reason)


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
    """The job's events.Signal while it is queued or running (_drop_finished_conditions removes it after the end)."""
    with _lock:
        return _conds.setdefault(job_id, events.Signal())


def _drop_finished_conditions():
    """Drop the conditions of finished or missing jobs. Holders keep their reference: one taken before the end saw
    the end notify (Log.close), one taken after reads the final status before it waits, because the status is
    committed before Log.close and before this runs."""
    with _lock:
        ids = [i for i in _conds if i not in _running]
    if not ids:
        return
    live = {r[0] for r in db.conn().execute(
        f"SELECT id FROM jobs WHERE id IN ({','.join('?' * len(ids))}) AND status IN ('queued','running')", ids)}
    with _lock:
        for i in ids:
            if i not in live and i not in _running:
                _conds.pop(i, None)


def _killpg(pid, sig):
    """Signal the group led by pid. Gone group = done. macOS answers EPERM, not ESRCH, for a group whose only
    member is the exited unreaped leader: also done. Any other PermissionError (live leader) is real."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            exited = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:  # reaped: the group is gone
            exited = True
        if not exited:
            raise


def _kill_group(p):
    _killpg(p.pid, signal.SIGTERM)
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        _killpg(p.pid, signal.SIGKILL)


CHILD_ENV = ('PATH', 'LANG', 'TZ')
SOLVER_ENV = ('FLARESOLVERR_URL', 'CFBYPASS_URL', 'APKM_SOLVER_URL')  # Cloudflare solvers for runner downloads and fetches


def isolate(argv):
    """argv for third-party code (patch bundles, CLIs, untrusted APKs): runs as RELUME_RUNNER_UID, same gid, no
    supplementary groups, no capabilities, no new privileges. Unset (tests, bare image) = the server's own uid."""
    uid = os.environ.get('RELUME_RUNNER_UID')
    if not uid:
        return list(argv)
    return ['setpriv', f'--reuid={uid}', f'--regid={os.getgid()}', '--clear-groups',
            '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs', *argv]


def run_isolated(log, argv, home, env_keys=(), env=None, timeout=None, on_line=None):
    """log.run of third-party code: isolated uid (isolate), cwd and HOME = home, and only the CHILD_ENV + env_keys
    server variables plus the env extras. No GITHUB_TOKEN or other server secret; HOME is set last because the
    server's HOME holds the adb key. A module function, not a Log method, so test log stand-ins work unchanged."""
    child = {k: os.environ[k] for k in (*CHILD_ENV, *env_keys) if k in os.environ}
    child.update(env or {})
    child['HOME'] = home
    return log.run(isolate(argv), cwd=home, env=child, timeout=timeout, on_line=on_line)


@contextlib.contextmanager
def isolated_scratch(prefix):
    """Scratch dir for one isolated run: under DATA/work, which the runner sees through runner_path, mode 0770 so
    the runner uid (same gid) can write, deleted on exit (a cleanup failure raises, as TemporaryDirectory does).
    Pass runner_path(dir) to the child, read its files with open_runner_file(dir, ...)."""
    with tempfile.TemporaryDirectory(prefix=prefix, dir=db.DATA / 'work') as d:
        os.chmod(d, 0o770)
        yield pathlib.Path(d)


class RequestLog:
    """Log stand-in for a run in a request handler (no job, nothing to cancel): lines go to the server log."""

    def __init__(self, prefix):
        self.prefix = prefix

    def line(self, text):
        print(f'{self.prefix}: {text}', flush=True)

    def run(self, argv, cwd=None, env=None, timeout=None, on_line=None):
        try:
            p = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors='replace', timeout=timeout)
        except subprocess.TimeoutExpired:
            raise JobTimeout()
        for s in p.stdout.splitlines():
            self.line(s)
            if on_line:
                on_line(s)
        return p.returncode


def runner_path(p):
    """Path under db.DATA as the isolated runner sees it: compose mounts work, apk-cache and upstream at
    RELUME_RUNNER_DATA because DATA itself sits in a directory the runner uid cannot enter."""
    root = os.environ.get('RELUME_RUNNER_DATA')
    return os.path.join(root, os.path.relpath(p, db.DATA)) if root else str(p)


def open_runner_file(base, rel):
    """Binary file object for base/rel, a file the isolated runner wrote. No symlink is followed anywhere below
    base (every component is opened O_NOFOLLOW relative to its parent fd) and the open fd must be a regular file
    with one link, so a runner leftover cannot swap in a link to a secret. Raises OSError."""
    parts = pathlib.PurePath(rel).parts
    if not parts or pathlib.PurePath(rel).is_absolute() or '..' in parts:
        raise OSError(f'bad runner path: {rel}')
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nfd
        f = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    st = os.fstat(f)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(f)
        raise OSError(f'not a regular file: {rel}')
    return os.fdopen(f, 'rb')


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

        def watch():  # woken by cancel() and by the process end below, both notify self.cond
            with self.cond:
                ended = self.cond.wait_for(lambda: done.is_set() or self.job.cancel_event.is_set(), timeout or None)
            if done.is_set():
                return
            if not ended:
                timed_out.set()
            _kill_group(p)

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
            try:
                p.stdout.close()
                if p.returncode is None:
                    try:  # wait without reaping: the leader's pid (= the group id) cannot be reused before the kill
                        os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOWAIT)
                    except ChildProcessError:  # the watch thread reaped it
                        pass
                _killpg(p.pid, signal.SIGKILL)  # nothing of the group outlives the job, also on success
                rc = p.wait()
            finally:  # the watch thread ends with the process, also when the cleanup above raises
                with self.cond:
                    done.set()
                    self.cond.notify_all()
        if timed_out.is_set():
            raise JobTimeout()
        if self.job.cancel_event.is_set():
            raise JobCancelled()
        return rc

    def close(self):
        self.f.close()
        with self.cond:
            self.cond.notify_all()


def register(kind, handler, detail=None):
    _handlers[kind] = (handler, detail)
    _notify()  # a worker of this kind may have skipped its queued jobs while no handler was registered


def _notify():
    with _wake:
        _wake.notify_all()


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
    _notify()
    events.changed('jobs')
    return jid


def _reason(e):
    """'<Type>: <first line of the message>', or the bare type name when the message is empty."""
    first = (str(e).splitlines() or [''])[0]
    return f'{type(e).__name__}: {first}' if first else type(e).__name__


def _execute(job):
    handler = _handlers[job.kind][0]
    log = Log(job)
    try:
        with _lock:
            _running[job.id] = job
        status, reason = 'success', None
        try:
            handler(job, log)
        except JobFailed as e:
            status, reason = 'failed', e.reason
        except Exception as e:
            status, reason = 'failed', _reason(e)
            log.line(traceback.format_exc())
        finally:
            with _lock:
                _running.pop(job.id, None)
        if job.cancel_event.is_set():
            status, reason = 'cancelled', 'cancelled'
        with db.tx() as c:
            # a handler that returns normally may have written jobs.reason itself (OTA outcome)
            c.execute('UPDATE jobs SET status=?, reason=COALESCE(?, reason), finished_at=? WHERE id=?',
                      (status, reason, db.utcnow(), job.id))
    finally:
        log.close()
    _drop_finished_conditions()
    _cleanup(job.kind)
    events.changed('jobs')  # job end and every DB write its handler made
    return status


def _abort(job, e):
    """_execute raised outside the handler (log file, status write, log close, cleanup). Called inside the
    worker's except: prints the error, marks the job failed on a fresh connection (a failed COMMIT can leave the
    old one inside its transaction) unless a final status is already written, and wakes the job's log streams.
    If that write fails too the row stays 'running' until the next start (recover); the worker continues."""
    print(f'jobs: {job.kind} job {job.id} raised outside its handler ({_reason(e)}); '
          f'the {job.kind} worker continues\n{traceback.format_exc()}', flush=True)
    db.close()
    try:
        with db.tx() as c:
            c.execute("UPDATE jobs SET status='failed', reason=?, finished_at=? WHERE id=? AND status='running'",
                      (f'internal error: {_reason(e)}', db.utcnow(), job.id))
    except Exception as e2:  # handled: printed with job, kind and cause; the row keeps its stored state
        print(f'jobs: {job.kind} job {job.id}: cannot record the failure ({_reason(e2)}); '
              f'the row stays running until the next server start\n{traceback.format_exc()}', flush=True)
        db.close()
    _wake_job_waiters()  # log streams re-read the status
    events.changed('jobs')


def _claim(kind):
    with db.tx() as c:
        r = c.execute("SELECT * FROM jobs WHERE kind=? AND status='queued' ORDER BY id LIMIT 1",
                      (kind,)).fetchone()
        if r is None:
            return None
        c.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (db.utcnow(), r['id']))
    events.changed('jobs')
    return Job(r)


def _worker(kind):
    try:
        while True:
            seen = _wake.mark()  # before the stop check: a stop() after it changes seq, one before it is seen
            if _stop.is_set():
                return
            try:
                job = _claim(kind) if kind in _handlers else None
            except Exception as e:  # nothing claimed: the claim is one transaction, close drops a half-open one
                print(f'jobs: the {kind} worker could not claim a queued job ({_reason(e)}); '
                      f'it claims again at the next wake\n{traceback.format_exc()}', flush=True)
                db.close()
                job = None
            if job is None:
                _wake.wait_change(seen)  # stop() sets _stop, then notifies
                continue
            try:
                _execute(job)
            except Exception as e:  # outside the handler: see _abort
                _abort(job, e)
    finally:
        db.close()


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
    _notify()
    for t in _threads:
        t.join(15)
    _threads.clear()


def cancel(job_id):
    with db.tx() as c:
        n = c.execute("UPDATE jobs SET status='cancelled', reason='cancelled', finished_at=? "
                      "WHERE id=? AND status='queued'", (db.utcnow(), job_id)).rowcount
    if n:
        _wake_job_waiters()
        _drop_finished_conditions()
        events.changed('jobs')
        return 'cancelled'
    with _lock:
        job = _running.get(job_id)
    if job:
        job.cancel_event.set()
        _wake_job_waiters()
        return 'cancelling'
    return None


def _wake_job_waiters():
    """A cancel notifies every job's condition, not only its own: a job may wait on another job's condition
    (ota pulling a project waits for the upstream job and for its own cancel). Waiters recheck their state.
    Only live jobs have a condition."""
    with _lock:
        conds = list(_conds.values())
    for cond in conds:
        with cond:
            cond.notify_all()


def recover():
    with db.tx() as c:
        c.execute("UPDATE jobs SET status='failed', reason='server restarted', finished_at=? "
                  "WHERE status='running'", (db.utcnow(),))


def _cleanup(kind):
    """Keep the newest job_history_count finished jobs of the kind; a job whose artifacts are published stays
    (artifacts.job_id is ON DELETE RESTRICT, the only RESTRICT reference to jobs)."""
    keep = int(settings.get('job_history_count', settings.JOB_HISTORY_DEFAULT))
    with db.tx() as c:
        gone = [r[0] for r in c.execute(
            "DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE kind = ? AND status NOT IN ('queued','running') "
            'ORDER BY id DESC LIMIT -1 OFFSET ?) AND id NOT IN (SELECT job_id FROM artifacts) RETURNING id',
            (kind, keep)).fetchall()]
    for jid in gone:
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


def log_stream(job_id, offset=0):
    path = db.DATA / 'logs' / f'{job_id}.log'
    cond, last = None, time.monotonic()
    while True:
        seen = cond.mark() if cond else None
        row = db.conn().execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
        done = row is None or row['status'] not in ('queued', 'running')
        if not done and cond is None:  # a live job: take its condition, then look again before the first wait
            cond = log_condition(job_id)
            continue
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
        cond.wait_change(seen, max(0, events.KEEPALIVE - (time.monotonic() - last)))  # lines, job end, cancel notify
        if time.monotonic() - last >= events.KEEPALIVE:
            yield b': keepalive\n\n'
            last = time.monotonic()


def _int(q, key):
    try:
        return int(q[key])
    except ValueError:
        raise HttpError(400, f'{key} expected an integer, got {q[key]!r}')


def list_route(req):
    q, where, args = req.query, [], []
    if 'kind' in q:   # one kind or a comma list (builds page: build,upstream)
        kinds = q['kind'].split(',')
        where.append(f"kind IN ({','.join('?' * len(kinds))})")
        args.extend(kinds)
    if 'status' in q:
        where.append('status=?')
        args.append(q['status'])
    for key in ('item_id', 'device_id'):
        if key in q:
            where.append(f"json_extract(target,'$.{key}')=?")
            args.append(_int(q, key))
    if 'schedule_id' in q:   # update-run outcome strip (web Schedules page, decision 12): manual runs carry the id too, and so do the build jobs a run queues (the strip adds kind=upstream)
        where.append('schedule_id=?')
        args.append(_int(q, 'schedule_id'))
    if 'before_id' in q:
        where.append('id<?')
        args.append(_int(q, 'before_id'))
    limit = max(1, min(_int(q, 'limit'), 100)) if 'limit' in q else 50  # LIMIT <0 = unlimited in SQLite
    sql = 'SELECT * FROM jobs' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY id DESC LIMIT ?'
    return 200, {'jobs': [to_json(r) for r in db.conn().execute(sql, (*args, limit))]}


def _row_or_404(job_id):
    r = db.conn().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if r is None:
        raise HttpError(404, 'job not found')
    return r


def get_route(req):
    return 200, to_json(_row_or_404(req.params['id']))


def cancel_route(req):
    _row_or_404(req.params['id'])
    res = cancel(req.params['id'])
    if res is None:
        raise HttpError(409, 'job already finished')
    return 200, {'status': res}


def log_route(req):
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
