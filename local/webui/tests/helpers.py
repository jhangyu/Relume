import atexit
import json
import pathlib
import shutil
import socket
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request

from webui import auth, db, events, jobs, server, settings


_templates = {}  # schema_dir -> data dir holding a migrated, closed (no -wal) database


def _template(schema_dir):
    """Migrate once per process per schema_dir; every test copies the result instead of migrating again."""
    if schema_dir not in _templates:
        tmp = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, tmp, True)
        db.init(tmp)
        db.migrate(schema_dir)
        db.close()  # last connection of a WAL database checkpoints and removes -wal/-shm
        _templates[schema_dir] = tmp
    return _templates[schema_dir]


class DataTestCase(unittest.TestCase):
    """Fresh RELUME_DATA dir holding a copy of the migrated schema database for every test."""
    schema_dir = None

    def setUp(self):
        jobs.stop()                     # no worker from an earlier test may claim this test's jobs
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(jobs.stop)      # runs before tmp.cleanup (LIFO): no worker touches a removed dir
        self.addCleanup(db.close)       # this thread's connection; the last test's would finalize unclosed
        self.data = pathlib.Path(tmp.name)
        shutil.copytree(_template(self.schema_dir), self.data, dirs_exist_ok=True)
        db.init(self.data)
        events.reset()
        jobs.reset()
        auth.reset_lockout()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def serve(tc, names=()):
    """Live server on 127.0.0.1:<free port>; names = module names or already-imported modules."""
    mods = [server.load_modules([n])[0] if isinstance(n, str) else n for n in names]
    srv = server.make_server(mods, '127.0.0.1', 0)
    run_server(tc, srv)
    return f'http://127.0.0.1:{srv.server_address[1]}'


def run_server(tc, srv):
    """Serve srv in a thread until the test's cleanup. Not serve_forever: its shutdown() waits for the next
    0.5 s select poll. Here the cleanup sets the stop flag, then one connection wakes the blocked accept, so the
    loop ends at once (a connection queued before the loop reaches select also returns it immediately)."""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            srv.handle_request()

    t = threading.Thread(target=loop, daemon=True)
    t.start()

    def close():
        stop.set()
        socket.create_connection(srv.server_address).close()
        t.join()
        srv.server_close()

    tc.addCleanup(close)


def fake_request(method='GET', path='/', json=None, params=None, query=None, headers=None, session='test',
                 device_id=None):
    """Route-handler input without HTTP (auth, CSRF and body parsing are tested in test_core_*)."""
    return types.SimpleNamespace(method=method, path=path, json=json or {}, params=params or {},
                                 query=query or {}, headers=headers or {}, session=session, device_id=device_id)


def http(method, url, body=None, cookie=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {'Content-Type': 'application/json'} if method != 'GET' else {}
    if cookie:
        h['Cookie'] = cookie
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with _opener.open(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, dict(e.headers), e.read()


def login(base_url, password='correct-horse-42', username='admin'):
    settings.put('admin_username', username)
    auth.set_password(password)
    st, h, body = http('POST', base_url + '/api/auth/login', {'username': username, 'password': password, 'remember': True})
    assert st == 200, (st, body)
    return h['Set-Cookie'].split(';')[0]


# Runs one job in the calling thread through the worker's executor (needs no worker thread).
def run_inline(kind, target, trigger, options=None):
    with db.tx() as c:
        jid = c.execute('INSERT INTO jobs(kind,status,trigger,target,options,created_at,started_at) '
                        "VALUES (?,'running',?,?,?,?,?)",
                        (kind, trigger, jobs.canon(target), jobs.canon(jobs.normalize(kind, options)),
                         db.utcnow(), db.utcnow())).lastrowid
    jobs._execute(jobs.Job(db.conn().execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()))
    return jobs.get(jid)
