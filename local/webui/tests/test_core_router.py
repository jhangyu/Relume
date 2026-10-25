import http.client as httpclient, pathlib, sqlite3, sys, tempfile, threading, types, unittest
from unittest import mock
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


class FileResponseTest(PublicRoutesMixin, DataTestCase):
    BODY = bytes(range(100))

    def setUp(self):
        super().setUp()
        f = self.data / 'app.apk'
        f.write_bytes(self.BODY)
        hdr = {'X-Relume-Sha256': 'ab' * 32}
        routes = [('GET', '/api/file', lambda req: server.FileResponse(f, 'app.apk', etag='tag1', headers=hdr)),
                  ('GET', '/api/noetag', lambda req: server.FileResponse(f, 'app.apk'))]
        for route in routes:
            self.public(route[0], route[1])
        self.base = serve(self, [fake_module('fakefile', routes)])

    def get(self, **headers):
        return http('GET', self.base + '/api/file', headers=headers)

    def test_range_requests(self):
        st, h, body = self.get()
        self.assertEqual((st, body, h['Accept-Ranges'], h['ETag'], h['X-Relume-Sha256']),
                         (200, self.BODY, 'bytes', '"tag1"', 'ab' * 32))
        self.assertIn('attachment', h['Content-Disposition'])
        for rng, part, cr in (('bytes=0-9', self.BODY[:10], 'bytes 0-9/100'), ('bytes=10-', self.BODY[10:], 'bytes 10-99/100'),
                              ('bytes=-5', self.BODY[-5:], 'bytes 95-99/100'), ('bytes=90-500', self.BODY[90:], 'bytes 90-99/100')):
            st, h, body = self.get(Range=rng)
            self.assertEqual((st, body, h['Content-Range'], h['Content-Length']), (206, part, cr, str(len(part))), rng)
        for bad in ('bytes=0-1,5-6', 'bytes=100-', 'bytes=5-2', 'bytes=-0', 'bytes=-', 'items=0-1', 'bytes=x-1'):
            st, h, _ = self.get(Range=bad)
            self.assertEqual((st, h['Content-Range']), (416, 'bytes */100'), bad)

    def test_if_range_mismatch_sends_full_body(self):
        st, h, body = self.get(Range='bytes=0-9', **{'If-Range': '"other"'})
        self.assertEqual((st, body), (200, self.BODY))
        self.assertNotIn('Content-Range', h)
        st, _, body = self.get(Range='bytes=0-9', **{'If-Range': '"tag1"'})
        self.assertEqual((st, body), (206, self.BODY[:10]))

    def test_if_range_never_matches_without_etag(self):
        st, h, body = http('GET', self.base + '/api/noetag', headers={'Range': 'bytes=0-9', 'If-Range': '"None"'})
        self.assertEqual((st, body), (200, self.BODY))
        self.assertNotIn('Content-Range', h)

    def test_file_shrinking_mid_transfer_aborts_without_writing_an_error_into_the_body(self):
        grown = mock.Mock(st_size=len(self.BODY) + 50)  # fstat saw a bigger file than the read finds
        with mock.patch.object(server.os, 'fstat', return_value=grown), mock.patch('traceback.print_exc') as logged:
            with self.assertRaises(httpclient.IncompleteRead) as cm:
                self.get()
        self.assertEqual(cm.exception.partial, self.BODY)
        logged.assert_called_once()


class StaticTest(DataTestCase):
    def test_static_png_and_manifest_types(self):
        d = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        (d / 'icon-192.png').write_bytes(b'\x89PNG\r\n')
        (d / 'manifest.webmanifest').write_text('{"name": "Relume"}')
        self.enterContext(mock.patch.object(server, 'STATIC', d))
        base = serve(self)
        st, h, body = http('GET', base + '/static/icon-192.png')
        self.assertEqual((st, h['Content-Type'], body), (200, 'image/png', b'\x89PNG\r\n'))
        st, h, body = http('GET', base + '/static/manifest.webmanifest')   # no cookie: static assets are public
        self.assertEqual((st, h['Content-Type'], body), (200, 'application/manifest+json', b'{"name": "Relume"}'))


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
        self.assertEqual(server.validate_repo('o/r'), 'o/r')
        for url in ('https://github.com/o/r', 'http://www.github.com/o/r/', 'https://github.com/o/r.git',
                    'https://github.com/o/r/releases/tag/v1'):
            self.assertEqual(server.validate_repo(url), 'o/r')
        with self.assertRaises(server.HttpError):
            server.validate_repo('https://gitlab.com/o/r')
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
