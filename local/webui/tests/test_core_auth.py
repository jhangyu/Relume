import concurrent.futures, contextlib, datetime, hashlib, io, json, pathlib, re, tempfile, threading, unittest
from unittest import mock
from webui import auth, db, server, settings
from webui.tests.helpers import DataTestCase, serve, http, login


class AuthTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(auth, 'SCRYPT_N', 2 ** 4))   # fast hashing, the KDF cost is not under test
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

    def test_login_needs_account(self):
        self.assertEqual(http('POST', self.base + '/api/auth/login', {'username': 'a', 'password': 'b'})[0], 409)


DEVICE = {'name': 'Pixel 8', 'model': 'Pixel 8', 'sdk': 35, 'android_release': '15', 'abi_list': ['arm64-v8a']}


class DeviceTokenTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(auth, 'SCRYPT_N', 2 ** 4))   # fast hashing, the KDF cost is not under test
        self.base = serve(self, ['devices', 'notify', 'projects'])
        self.cookie = login(self.base)

    def register(self, password='correct-horse-42', device=DEVICE):
        return http('POST', self.base + '/api/auth/device', {'username': 'admin', 'password': password, 'device': device})

    def token(self):
        st, h, body = self.register()
        self.assertEqual(st, 200, body)
        self.assertNotIn('Set-Cookie', h)
        return json.loads(body)

    def bearer(self, method, path, token, body=None, cookie=None):
        return http(method, self.base + path, body, cookie=cookie, headers={'Authorization': f'Bearer {token}'})

    def bad_login(self):
        return http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'nope'})

    def test_bearer_token_paths(self):
        db.conn().execute("INSERT INTO devices(name, serial, host, connect_port, model) VALUES ('Tab', 's:5555', 's', 5555, 'T1')")
        out = self.token()
        tok = out['token']
        self.assertEqual(out['adb_devices'], [{'id': 1, 'name': 'Tab', 'model': 'T1'}])
        row = db.conn().execute('SELECT * FROM devices WHERE id=?', (out['device']['id'],)).fetchone()
        self.assertEqual((row['kind'], row['token_hash'], row['abi_list'], row['host']),
                         ('app', hashlib.sha256(tok.encode()).hexdigest(), 'arm64-v8a', None))
        self.assertEqual(self.bearer('GET', '/api/jobs', tok)[0], 200)
        for header in ('Bearer nope', 'bearer nope', 'Bearer '):   # no fall-through to the valid cookie
            st, _, body = http('GET', self.base + '/api/jobs', cookie=self.cookie, headers={'Authorization': header})
            self.assertEqual((st, json.loads(body)), (401, {'error': 'invalid device token'}), header)
        basic = {'Authorization': 'Basic dXNlcjpwYXNz'}   # reverse-proxy auth: ignored, the cookie path applies
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=self.cookie, headers=basic)[0], 200)
        st, _, body = http('GET', self.base + '/api/jobs', headers=basic)
        self.assertEqual((st, json.loads(body)), (401, {'error': 'login required'}))
        st, _, body = http('POST', self.base + '/api/me/logout', {}, cookie=self.cookie)
        self.assertEqual((st, json.loads(body)), (403, {'error': 'device token required'}))
        for bad in ({**DEVICE, 'sdk': '35'}, {**DEVICE, 'abi_list': ['mips']}, {**DEVICE, 'name': ''}, None):
            self.assertEqual(self.register(device=bad)[0], 400, bad)
        # revoke from the web UI: row stays, token stops working
        self.assertEqual(http('DELETE', self.base + '/api/devices/1/token', cookie=self.cookie)[:3:2],
                         (409, b'{"error": "not an app device"}'))
        self.assertEqual(http('DELETE', self.base + '/api/devices/99/token', cookie=self.cookie)[0], 404)
        did = out['device']['id']
        st, _, body = http('DELETE', self.base + f'/api/devices/{did}/token', cookie=self.cookie)
        self.assertEqual((st, json.loads(body)['device']['id']), (200, did))
        self.assertEqual(self.bearer('GET', '/api/jobs', tok)[0], 401)
        # app logout
        tok2 = self.token()['token']
        self.assertEqual(self.bearer('POST', '/api/me/logout', tok2, {})[:3:2], (200, b'{"ok": true}'))
        self.assertEqual(self.bearer('GET', '/api/jobs', tok2)[0], 401)
        self.assertEqual(db.conn().execute("SELECT count(*) FROM devices WHERE kind='app'").fetchone()[0], 2)

    def test_session_only_routes_reject_token(self):
        self.assertEqual(auth.SESSION_ONLY, {
            ('POST', '/api/auth/password'), ('POST', '/api/auth/logout'), ('PUT', '/api/settings'),
            ('PUT', '/api/notify'), ('DELETE', '/api/devices/{id}'), ('DELETE', '/api/devices/{id}/token')})
        tok = self.token()['token']
        for method, pattern in sorted(auth.SESSION_ONLY):
            st, _, body = self.bearer(method, pattern.replace('{id}', '1'), tok, {})
            self.assertEqual((st, json.loads(body)), (403, {'error': 'session required'}), pattern)
        self.assertEqual(db.conn().execute("SELECT count(*) FROM devices").fetchone()[0], 1)

    def test_signer_accept_allows_token(self):
        tok = self.token()['token']
        st, _, body = self.bearer('POST', '/api/projects/9/signer/accept', tok, {'signer_sha256': 'a' * 64})
        self.assertNotIn(st, (401, 403), body)
        self.assertEqual(st, 404, body)

    def test_device_registration_shares_lockout(self):
        for _ in range(2):
            self.assertEqual(self.bad_login()[0], 401)
        st, _, body = self.register(password='nope')
        self.assertEqual((st, json.loads(body)), (401, {'error': 'wrong credentials', 'attempts_left': 2}))
        self.assertEqual(json.loads(self.bad_login()[2])['attempts_left'], 1)
        self.assertEqual(json.loads(self.register(password='nope')[2])['error'], 'locked')
        st, _, body = self.register()      # right password, still locked: same counter as the login form
        self.assertEqual((st, json.loads(body)['error']), (429, 'locked'))
        self.assertEqual(db.conn().execute("SELECT count(*) FROM devices").fetchone()[0], 0)

    def test_bad_bearer_does_not_count_lockout(self):
        for _ in range(auth.MAX_FAILS + 1):
            self.assertEqual(self.bearer('GET', '/api/jobs', 'guess')[0], 401)
        self.assertEqual(json.loads(self.bad_login()[2])['attempts_left'], auth.MAX_FAILS - 1)

    def test_env_password_and_password_change_keep_device_tokens(self):
        tok = self.token()['token']
        before = db.conn().execute("SELECT token_hash FROM devices").fetchone()[0]
        st, _, _ = http('POST', self.base + '/api/auth/password', {'old': 'correct-horse-42', 'new': 'long-enough-pw'},
                        cookie=self.cookie)
        self.assertEqual(st, 200)
        self.assertEqual(http('GET', self.base + '/api/jobs', cookie=self.cookie)[0], 200)
        cookie = login(self.base, 'long-enough-pw')
        out = io.StringIO()
        with mock.patch.dict('os.environ', {auth.ADMIN_PASSWORD_ENV: 'from-env-password'}), contextlib.redirect_stdout(out):
            auth.apply_env_password()
        self.assertNotIn('from-env-password', out.getvalue())
        for old in (self.cookie, cookie):
            self.assertEqual(http('GET', self.base + '/api/jobs', cookie=old)[0], 401)
        self.assertEqual(http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'long-enough-pw'})[0], 401)
        self.assertEqual(http('POST', self.base + '/api/auth/login', {'username': 'admin', 'password': 'from-env-password'})[0], 200)
        self.assertEqual(self.bearer('GET', '/api/jobs', tok)[0], 200)
        self.assertEqual(db.conn().execute("SELECT token_hash FROM devices").fetchone()[0], before)
