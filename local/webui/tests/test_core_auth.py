import contextlib, datetime, io, pathlib, tempfile, unittest
from webui import auth, db, server, settings
from webui.tests.helpers import DataTestCase, serve, http, login


class AuthTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.base = serve(self)

    def test_hash_roundtrip(self):
        h = auth.hash_password('secret-pass-1')
        self.assertTrue(h.startswith('scrypt$'))
        self.assertTrue(auth.verify_password('secret-pass-1', h))
        self.assertFalse(auth.verify_password('other', h))
        self.assertFalse(auth.verify_password('x', None))

    def test_401_without_cookie(self):
        self.assertEqual(http('GET', self.base + '/api/jobs')[0], 401)

    def test_remember_true_and_false(self):
        auth.set_password('correct-horse-42')
        st, h, _ = http('POST', self.base + '/api/auth/login', {'password': 'correct-horse-42', 'remember': True})
        self.assertEqual(st, 200)
        self.assertIn('Max-Age=2592000', h['Set-Cookie'])
        self.assertIn('HttpOnly', h['Set-Cookie']); self.assertIn('SameSite=Strict', h['Set-Cookie'])
        st, h2, _ = http('POST', self.base + '/api/auth/login', {'password': 'correct-horse-42', 'remember': False})
        self.assertNotIn('Max-Age', h2['Set-Cookie'])
        rows = sorted(r[0] for r in db.conn().execute('SELECT expires_at FROM sessions'))
        now = datetime.datetime.now(datetime.timezone.utc)
        exp = [datetime.datetime.strptime(x, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc) - now for x in rows]
        self.assertAlmostEqual(exp[0].total_seconds(), 12 * 3600, delta=60)
        self.assertAlmostEqual(exp[1].total_seconds(), 30 * 86400, delta=60)

    def test_session_expiry(self):
        cookie = login(self.base)
        db.conn().execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=cookie)[0], 401)

    def test_lockout(self):
        auth.set_password('correct-horse-42')
        left = []
        for _ in range(4):
            st, _, body = http('POST', self.base + '/api/auth/login', {'password': 'nope'})
            self.assertEqual(st, 401)
            left.append(__import__('json').loads(body)['attempts_left'])
        self.assertEqual(left, [4, 3, 2, 1])
        st, _, body = http('POST', self.base + '/api/auth/login', {'password': 'nope'})
        self.assertEqual((st, __import__('json').loads(body)['error']), (429, 'locked'))
        st, _, body = http('POST', self.base + '/api/auth/login', {'password': 'correct-horse-42'})
        self.assertEqual(st, 429)
        self.assertGreater(__import__('json').loads(body)['retry_after'], 0)

    def test_logout(self):
        cookie = login(self.base)
        st, h, _ = http('POST', self.base + '/api/auth/logout', {}, cookie=cookie)
        self.assertEqual(st, 200)
        self.assertIn('Max-Age=0', h['Set-Cookie'])
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=cookie)[0], 401)

    def test_password_change(self):
        a = login(self.base)
        b = login(self.base)
        st, _, _ = http('POST', self.base + '/api/auth/password', {'old': 'correct-horse-42', 'new': 'short'}, cookie=a)
        self.assertEqual(st, 400)
        st, _, _ = http('POST', self.base + '/api/auth/password', {'old': 'bad', 'new': 'long-enough-pw'}, cookie=a)
        self.assertEqual(st, 403)
        st, _, body = http('POST', self.base + '/api/auth/password', {'old': 'correct-horse-42', 'new': 'long-enough-pw'}, cookie=a)
        self.assertEqual((st, body), (200, b'{"signed_out": 1}'))
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=b)[0], 401)
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=a)[0], 200)

    def test_ensure_password_prints_once(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            auth.ensure_password()
            auth.ensure_password()
        self.assertRegex(out.getvalue(), r'^admin password: \S{20}\n$')


class StaticRuleTest(DataTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = pathlib.Path(tmp.name)
        for n in ('index.html', 'login.html', 'item.html', 'x.css'):
            (d / n).write_text(n)
        old = server.STATIC
        server.STATIC = d
        self.addCleanup(setattr, server, 'STATIC', old)
        self.base = serve(self)

    def test_rule(self):
        self.assertEqual(http('GET', self.base + '/login.html')[0], 200)
        self.assertEqual(http('GET', self.base + '/static/x.css')[0], 200)
        for p in ('/', '/item.html'):
            st, h, _ = http('GET', self.base + p)
            self.assertEqual((st, h.get('Location')), (302, '/login.html'))
        cookie = login(self.base)
        self.assertEqual(http('GET', self.base + '/', cookie=cookie)[2], b'index.html')
        self.assertEqual(http('GET', self.base + '/static/item.html')[0], 404)
        self.assertEqual(http('GET', self.base + '/static/../db.py')[0], 404)
