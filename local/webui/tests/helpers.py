import json
import pathlib
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request

from webui import auth, db, events, jobs, server


class DataTestCase(unittest.TestCase):
    """Fresh RELUME_DATA dir + all schema files for every test."""
    schema_dir = None

    def setUp(self):
        jobs.stop()                     # no worker from an earlier test may claim this test's jobs
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(jobs.stop)      # runs before tmp.cleanup (LIFO): no worker touches a removed dir
        self.data = pathlib.Path(tmp.name)
        db.init(self.data)
        db.migrate(self.schema_dir)
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
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tc.addCleanup(srv.server_close)
    tc.addCleanup(srv.shutdown)
    return f'http://127.0.0.1:{srv.server_address[1]}'


def fake_request(method='GET', path='/', json=None, params=None, query=None, headers=None, session='test'):
    """Route-handler input without HTTP (auth, CSRF and body parsing are tested in test_core_*)."""
    return types.SimpleNamespace(method=method, path=path, json=json or {}, params=params or {},
                                 query=query or {}, headers=headers or {}, session=session)


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
        return e.code, dict(e.headers), e.read()


def login(base_url, password='correct-horse-42'):
    auth.set_password(password)
    st, h, body = http('POST', base_url + '/api/auth/login', {'password': password, 'remember': True})
    assert st == 200, (st, body)
    return h['Set-Cookie'].split(';')[0]
