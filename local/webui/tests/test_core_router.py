import sqlite3, sys, threading, types, unittest
from webui import server
from webui.tests.helpers import DataTestCase, serve, http


def fake_module(name, routes, register=None):
    m = types.ModuleType('webui.' + name)
    m.ROUTES = routes
    if register:
        m.register = register
    sys.modules['webui.' + name] = m
    return name


class PublicRoutesMixin:
    """Routes in auth.PUBLIC skip the session check; tests mark their fake routes public."""
    def public(self, method, pattern):
        from webui import auth
        auth.PUBLIC.add((method, pattern))
        self.addCleanup(auth.PUBLIC.discard, (method, pattern))


class RouterTest(PublicRoutesMixin, DataTestCase):
    def setUp(self):
        super().setUp()
        def echo(req):
            return 200, {'params': req.params, 'json': req.json, 'q': req.query}
        def boom(req):
            raise RuntimeError('x')
        def locked(req):
            raise sqlite3.OperationalError('database is locked')
        def fail(req):
            raise server.HttpError(409, 'busy item')
        routes = [('GET', '/api/t/{id}/x/{name}', echo), ('PATCH', '/api/t/{id}', echo),
                  ('PATCH', '/api/t/order', lambda req: (200, {'literal': True})),
                  ('GET', '/api/boom', boom), ('GET', '/api/locked', locked), ('POST', '/api/fail', fail)]
        for m, p, _ in routes:
            self.public(m, p)
        self.base = serve(self, [fake_module('fakerouter', routes)])

    def test_int_params_and_query(self):
        st, _, body = http('GET', self.base + '/api/t/12/x/abc?k=v')
        self.assertEqual(st, 200)
        self.assertIn(b'"params": {"id": 12, "name": "abc"}', body)
        self.assertIn(b'"q": {"k": "v"}', body)

    def test_literal_route_beats_param(self):
        self.assertEqual(http('PATCH', self.base + '/api/t/order', body={})[2], b'{"literal": true}')

    def test_404_405(self):
        self.assertEqual(http('GET', self.base + '/api/nope')[0], 404)
        self.assertEqual(http('DELETE', self.base + '/api/t/1', body={})[0], 405)

    def test_patch_mutating_rules(self):
        self.assertEqual(http('PATCH', self.base + '/api/t/1', body={'a': 1})[0], 200)
        st, _, _ = http('PATCH', self.base + '/api/t/1', body={'a': 1}, headers={'Content-Type': 'text/plain'})
        self.assertEqual(st, 415)
        st, _, _ = http('PATCH', self.base + '/api/t/1', body={}, headers={'Origin': 'http://evil.example'})
        self.assertEqual(st, 403)

    def test_non_object_body(self):
        self.assertEqual(http('PATCH', self.base + '/api/t/1', body=[1])[0], 400)

    def test_too_large(self):
        st, _, _ = http('PATCH', self.base + '/api/t/1', body={'a': 'x' * (1 << 20)})
        self.assertEqual(st, 413)

    def raw_patch(self, length):
        import socket, urllib.parse
        u = urllib.parse.urlsplit(self.base)
        with socket.create_connection((u.hostname, u.port), timeout=15) as s:
            s.sendall(f'PATCH /api/t/1 HTTP/1.1\r\nHost: {u.netloc}\r\nContent-Type: application/json\r\n'
                      f'Content-Length: {length}\r\n\r\n{{}}'.encode())
            s.shutdown(socket.SHUT_WR)  # a read(-1) would return here instead of hanging the test
            return int(s.makefile('rb').readline().split()[1])

    def test_bad_content_length_400(self):
        self.assertEqual(self.raw_patch('-1'), 400)
        self.assertEqual(self.raw_patch('abc'), 400)
        self.assertEqual(self.raw_patch('2'), 200)

    def test_error_mapping(self):
        self.assertEqual(http('GET', self.base + '/api/boom')[:3:2], (500, b'{"error": "internal error"}'))
        self.assertEqual(http('GET', self.base + '/api/locked')[0], 503)
        self.assertEqual(http('POST', self.base + '/api/fail', body={})[2], b'{"error": "busy item"}')


class LoadModulesTest(DataTestCase):
    def test_empty(self):
        self.assertEqual(server.load_modules(()), [])

    def test_register_starts_no_thread(self):
        name = fake_module('fakereg', [], register=lambda: None)
        before = threading.active_count()
        for m in server.load_modules([name]):
            m.register()
        self.assertEqual(threading.active_count(), before)


class ValidatorTest(unittest.TestCase):
    def test_slug(self):
        for ok in ('morphe', 'youtube-music-arm64-v8a', 'morphe.keystore', '_x'):
            self.assertEqual(server.validate_slug(ok), ok)
        for bad in ('', '.x', '-x', '..', 'a/b', 'a b', 5):
            with self.assertRaises(server.HttpError):
                server.validate_slug(bad)

    def test_package_repo_host_port(self):
        server.validate_package('com.google.android.youtube')
        server.validate_repo('MorpheApp/morphe-desktop')
        server.validate_host('192.168.1.20')
        server.validate_host('pixel-7a.local')
        self.assertEqual(server.validate_port(5555), 5555)
        for f, v in ((server.validate_package, 'youtube'), (server.validate_repo, 'a/b/c'),
                     (server.validate_host, '-bad'), (server.validate_host, '1.2.3.4; rm'),
                     (server.validate_port, 0), (server.validate_port, 65536), (server.validate_port, True)):
            with self.assertRaises(server.HttpError):
                f(v)

    def test_safe_remote(self):
        self.assertEqual(server.safe_remote('../x y'), '_._x_y')
        self.assertEqual(server.safe_remote('-v1.2'), '_v1.2')
