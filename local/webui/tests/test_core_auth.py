import concurrent.futures, contextlib, datetime, io, json, pathlib, re, tempfile, threading, unittest
from unittest import mock
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
        settings.put('admin_username', 'admin'); auth.set_password('correct-horse-42')
        st, h, _ = http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'correct-horse-42', 'remember': True})
        self.assertEqual(st, 200)
        self.assertIn('Max-Age=2592000', h['Set-Cookie'])
        self.assertIn('HttpOnly', h['Set-Cookie']); self.assertIn('SameSite=Strict', h['Set-Cookie'])
        st, h2, _ = http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'correct-horse-42', 'remember': False})
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
        settings.put('admin_username', 'admin'); auth.set_password('correct-horse-42')
        left = []
        for _ in range(4):
            st, _, body = http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'nope'})
            self.assertEqual(st, 401)
            left.append(__import__('json').loads(body)['attempts_left'])
        self.assertEqual(left, [4, 3, 2, 1])
        st, _, body = http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'nope'})
        self.assertEqual((st, __import__('json').loads(body)['error']), (429, 'locked'))
        st, _, body = http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'correct-horse-42'})
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

    def test_state_and_boot_silent(self):
        self.assertEqual(json.loads(http('GET', self.base + '/api/auth/state')[2]), {'setup_required': True})
        settings.put('admin_password_hash', auth.hash_password('x' * 10))   # hash without username = no account
        self.assertEqual(json.loads(http('GET', self.base + '/api/auth/state')[2]), {'setup_required': True})
        self.assertEqual(http('GET', self.base + '/api/jobs')[0], 401)

    def test_setup(self):
        url = self.base + '/api/auth/setup'
        for bad in ({'username': '', 'password': 'long-enough-pw'}, {'username': 'a b', 'password': 'long-enough-pw'},
                    {'username': 'x' * 65, 'password': 'long-enough-pw'}, {'username': 'admin', 'password': 'short'}, {}):
            self.assertEqual(http('POST', url, bad)[0], 400, bad)
        st, h, _ = http('POST', url, {'username': 'admin', 'password': 'long-enough-pw'})
        self.assertEqual(st, 200)
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=h['Set-Cookie'].split(';')[0])[0], 200)
        st, _, body = http('POST', url, {'username': 'other', 'password': 'long-enough-pw'})
        self.assertEqual((st, json.loads(body)), (409, {'error': 'already set up'}))
        self.assertEqual(json.loads(http('GET', self.base + '/api/auth/state')[2]), {'setup_required': False})

    def test_concurrent_setup_one_winner(self):
        url = self.base + '/api/auth/setup'
        go = threading.Barrier(4)

        def attempt(i):
            go.wait()
            return http('POST', url, {'username': f'u{i}', 'password': 'long-enough-pw'})[0]
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            codes = sorted(ex.map(attempt, range(4)))
        self.assertEqual(codes, [200, 409, 409, 409])
        self.assertRegex(settings.get('admin_username'), r'^u\d$')

    def test_login_same_401_for_wrong_user_or_password(self):
        login(self.base)
        a = http('POST', self.base + '/api/auth/login', {'username': 'nobody', 'password': 'correct-horse-42'})
        b = http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'wrong'})
        self.assertEqual((a[0], b[0]), (401, 401))
        self.assertEqual(json.loads(a[2])['error'], 'wrong credentials')
        self.assertEqual(json.loads(a[2])['attempts_left'], 4)
        self.assertEqual(json.loads(b[2])['attempts_left'], 3)

    def test_login_and_reset_need_account(self):
        self.assertEqual(http('POST', self.base + '/api/auth/login', {'username': 'a', 'password': 'b'})[0], 409)
        self.assertEqual(http('POST', self.base + '/api/auth/reset', {})[0], 409)

    def test_reset_cooldown_and_new_password(self):
        cookie = login(self.base)
        clock = [1000.0]
        self.enterContext(mock.patch.object(auth, '_clock', lambda: clock[0]))
        auth._state['reset_at'] = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st, _, body = http('POST', self.base + '/api/auth/reset', {})
        self.assertEqual((st, json.loads(body)), (202, {'ok': True}))
        m = re.fullmatch(r'admin password reset for admin: (\S{20})\n', out.getvalue())
        self.assertTrue(m, out.getvalue())
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=cookie)[0], 401)
        u = self.base + '/api/auth/login'
        self.assertEqual(http('POST', u, {'username': 'admin', 'password': 'correct-horse-42'})[0], 401)
        self.assertEqual(http('POST', u, {'username': 'admin', 'password': m.group(1)})[0], 200)
        clock[0] += auth.RESET_COOLDOWN - 1
        with contextlib.redirect_stdout(io.StringIO()) as quiet:
            st, _, body = http('POST', self.base + '/api/auth/reset', {})
        self.assertEqual((st, json.loads(body)), (429, {'error': 'cooldown', 'retry_after': 1}))
        self.assertEqual(quiet.getvalue(), '')
        clock[0] += 1
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(http('POST', self.base + '/api/auth/reset', {})[0], 202)
