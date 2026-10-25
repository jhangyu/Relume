import contextlib, io, json, os, pathlib, subprocess, sys, tempfile, types
from unittest import mock
from webui import build, settings
from webui.tests.helpers import DataTestCase, serve, http, login


class SettingsTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.enterContext(mock.patch.object(build, 'run_retention', lambda item_id=None: self.calls.append(item_id)))
        tok = self.data / 'token'
        tok.write_text('ghp_abcdefgh1234\n')
        self.enterContext(mock.patch.object(settings, 'TOKEN_PATH', tok))
        self.fetch = self.enterContext(mock.patch.object(settings, 'fetch_rate_limit', return_value=(
            200, {'resources': {'core': {'limit': 5000, 'remaining': 4990, 'reset': 1760000000}}},
            {'x-oauth-scopes': 'repo', 'github-authentication-token-expiration': '2026-12-01 00:00:00 UTC'})))
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
        self.assertEqual((d['ok'], d['app'], d['version']), (True, 'Relume', 'dev'))

    def test_defaults_and_put(self):
        s = self.get()
        self.assertEqual((s['job_history_count'], s['base_url'], s['artifact_retention']), (500, '', 1))
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
            self.assertIn('enter one on the Settings page', out.getvalue())
            self.assertEqual(settings.github_token(), '')

    def test_disk(self):
        (self.data / 'artifacts' / '1' / '2').mkdir(parents=True)
        (self.data / 'artifacts' / '1' / '2' / 'a.apk').write_bytes(b'x' * 10)
        (self.data / 'logs' / '1.log').write_bytes(b'y' * 3)
        disk = self.get()['server']['disk']
        self.assertEqual((disk['artifacts'], disk['artifacts_count'], disk['logs']), (10, 1, 3))
        self.assertGreater(disk['total'], 0)


def as_runner(cmd):
    """Run sh -c cmd as the isolated runner uid 10001 (same gid, no groups), like jobs.isolate."""
    return subprocess.run(['setpriv', '--reuid=10001', f'--regid={os.getgid()}', '--clear-groups', 'sh', '-c', cmd],
                          capture_output=True, text=True).stdout.strip()


class UiTokenTest(SettingsTest):
    """Token entered on the Settings page: write-only, preferred over the mounted file, unreadable by the runner."""

    def put(self, body):
        return http('PUT', self.base + '/api/settings', body, cookie=self.c)

    def test_put_token_used_never_returned(self):
        st, _, body = self.put({'github_token': 'github_pat_UI9876'})
        self.assertEqual(st, 200)
        self.assertNotIn(b'github_pat_UI9876', body)
        self.assertNotIn(b'github_pat_UI9876', http('GET', self.base + '/api/settings', cookie=self.c)[2])
        self.assertEqual(settings.github_token(), 'github_pat_UI9876')     # UI token wins over the file
        g = self.get('?recheck=1')['github']
        self.assertEqual((g['state'], g['source'], g['token_hint']), ('authenticated', 'ui', '****9876'))
        self.assertEqual(self.fetch.call_args[0][0], 'github_pat_UI9876')
        self.put({'github_token': ''})                                       # clear -> file fallback
        self.assertEqual(settings.github_token(), 'ghp_abcdefgh1234')
        self.assertEqual(self.get('?recheck=1')['github']['source'], 'file')
        settings.TOKEN_PATH.unlink()
        self.assertEqual(self.get('?recheck=1')['github']['state'], 'absent')
        for bad in ('has space', 'x' * 256, 5, 'a\nb'):
            self.assertEqual(self.put({'github_token': bad})[0], 400, bad)

    def check_output(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            settings.check_token_file()
        return out.getvalue()

    def test_dev_null_default_points_to_settings_page(self):
        # compose binds /dev/null when RELUME_GITHUB_TOKEN_FILE is unset: no file warning, a Settings-page hint instead
        with mock.patch.object(settings, 'TOKEN_PATH', pathlib.Path('/dev/null')):
            self.assertEqual(self.check_output(), 'no GitHub token set: enter one on the Settings page\n')
            self.assertEqual(settings.github_token(), '')
            self.assertEqual(self.get('?recheck=1')['github']['state'], 'absent')
            self.put({'github_token': 'ghp_ui0001'})
            self.assertEqual(settings.github_token(), 'ghp_ui0001')
            self.assertEqual(self.check_output(), '')          # UI token set: no message at all

    def test_startup_message_quiet_when_a_token_exists(self):
        self.assertEqual(self.check_output(), '')              # usable mounted file (setUp)
        d = self.data / 'tokdir'; d.mkdir()
        with mock.patch.object(settings, 'TOKEN_PATH', d):
            self.put({'github_token': 'ghp_ui0002'})
            self.assertEqual(self.check_output(), '')          # UI token wins: no file warning either

    def test_token_modes_and_runner_denied(self):
        self.put({'github_token': 'ghp_secret1234'})
        f = settings.ui_token_path()
        self.assertEqual((f.stat().st_mode & 0o777, f.parent.stat().st_mode & 0o777), (0o600, 0o700))
        if os.geteuid() != 0:
            self.skipTest('setpriv needs root (T_PY runs as root)')
        os.chmod(self.data, 0o755)  # TemporaryDirectory is 0700: open it so only the secrets modes are under test
        self.assertEqual(as_runner(f'ls {self.data} >/dev/null || echo NO; echo SEES_DATA'), 'SEES_DATA')  # instrument
        self.assertEqual(as_runner(f'cat {f} 2>/dev/null || echo DENIED'), 'DENIED')
