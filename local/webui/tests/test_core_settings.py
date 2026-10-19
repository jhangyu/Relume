import contextlib, io, json, pathlib, sys, tempfile, types
from unittest import mock
from webui import settings
from webui.tests.helpers import DataTestCase, serve, http, login


class SettingsTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        build = types.ModuleType('webui.build')
        build.run_retention = lambda item_id=None: self.calls.append(item_id)
        p = mock.patch.dict(sys.modules, {'webui.build': build}); p.start(); self.addCleanup(p.stop)
        tok = self.data / 'token'
        tok.write_text('ghp_abcdefgh1234\n')
        p2 = mock.patch.object(settings, 'TOKEN_PATH', tok); p2.start(); self.addCleanup(p2.stop)
        self.gh = mock.patch.object(settings, 'fetch_rate_limit', return_value=(
            200, {'resources': {'core': {'limit': 5000, 'remaining': 4990, 'reset': 1760000000}}},
            {'X-OAuth-Scopes': 'repo', 'github-authentication-token-expiration': '2026-12-01 00:00:00 UTC'}))
        self.fetch = self.gh.start(); self.addCleanup(self.gh.stop)
        settings._gh_cache.clear()
        self.base = serve(self)
        self.c = login(self.base)

    def get(self, q=''):
        return json.loads(http('GET', self.base + '/api/settings' + q, cookie=self.c)[2])

    def test_health_public_exact_keys(self):
        st, _, body = http('GET', self.base + '/api/health')
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertEqual(sorted(d), ['app', 'ok', 'started_at', 'version'])
        self.assertEqual((d['ok'], d['app'], d['version']), (True, 'Patchbay', '1.0'))

    def test_defaults_and_put(self):
        s = self.get()
        self.assertEqual((s['job_history_count'], s['base_url'], s['artifact_retention']), (500, '', 3))
        st, _, _ = http('PUT', self.base + '/api/settings', {'artifact_retention': 5, 'base_url': 'http://nas:47000/'}, cookie=self.c)
        self.assertEqual(st, 200)
        self.assertEqual(self.calls, [None])
        http('PUT', self.base + '/api/settings', {'artifact_retention': 5}, cookie=self.c)
        self.assertEqual(self.calls, [None])       # unchanged -> no second run
        self.assertEqual(self.get()['base_url'], 'http://nas:47000')
        for bad in ({'artifact_retention': 0}, {'job_history_count': True}, {'base_url': 'ftp://x'}, {'admin_password_hash': 'x'}):
            self.assertEqual(http('PUT', self.base + '/api/settings', bad, cookie=self.c)[0], 400)

    def test_github_authenticated_and_cache(self):
        g = self.get()['github']
        self.assertEqual((g['state'], g['limit'], g['remaining'], g['token_hint'], g['scopes']),
                         ('authenticated', 5000, 4990, '****1234', 'repo'))
        self.assertEqual(g['expires_at'], '2026-12-01 00:00:00 UTC')
        self.get(); self.assertEqual(self.fetch.call_count, 1)
        self.get('?recheck=1'); self.assertEqual(self.fetch.call_count, 2)

    def test_github_other_states(self):
        self.fetch.return_value = (401, {}, {})
        self.assertEqual(self.get('?recheck=1')['github']['state'], 'rejected')
        settings.TOKEN_PATH.write_text('')
        self.fetch.return_value = (200, {'resources': {'core': {'limit': 60, 'remaining': 60, 'reset': 1}}}, {})
        g = self.get('?recheck=1')['github']
        self.assertEqual((g['state'], g['token_hint'], g['expires_at']), ('unauthenticated', None, None))
        settings.TOKEN_PATH.unlink()
        self.assertEqual(self.get('?recheck=1')['github']['state'], 'absent')

    def test_token_path_is_directory(self):
        d = self.data / 'tokdir'; d.mkdir()
        with mock.patch.object(settings, 'TOKEN_PATH', d):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                settings.check_token_file()
            self.assertIn('github token path is not a file', out.getvalue())
            self.assertEqual(settings.github_token(), '')

    def test_disk(self):
        (self.data / 'artifacts' / '1' / '2').mkdir(parents=True)
        (self.data / 'artifacts' / '1' / '2' / 'a.apk').write_bytes(b'x' * 10)
        (self.data / 'logs' / '1.log').write_bytes(b'y' * 3)
        disk = self.get()['server']['disk']
        self.assertEqual((disk['artifacts'], disk['artifacts_count'], disk['logs']), (10, 1, 3))
        self.assertGreater(disk['total'], 0)
