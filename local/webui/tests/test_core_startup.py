import concurrent.futures, contextlib, io, os, pathlib, subprocess, sys, threading, types, unittest
from unittest import mock
from webui import auth, db, server, settings
from webui.tests.helpers import DataTestCase
import webui.__main__ as cli


class StartupTest(DataTestCase):
    def fake(self, name, calls):
        m = types.ModuleType('webui.' + name)
        m.ROUTES = []
        m.register = lambda: calls.append(name + '.register')
        m.start = lambda: calls.append(name + '.start')
        if name == 'catalog':
            m.seed_import = lambda: calls.append('catalog.seed_import')
        self.enterContext(mock.patch.dict(sys.modules, {'webui.' + name: m}))
        return name

    def test_boot_order_no_threads(self):
        calls = []
        names = [self.fake('catalog', calls), self.fake('build', calls)]
        before = threading.active_count()
        with mock.patch.dict('os.environ', {'RELUME_DATA': str(self.data)}):
            cli.boot(names=names)
        self.assertEqual(calls, ['catalog.seed_import', 'catalog.register', 'build.register'])
        self.assertEqual(threading.active_count(), before)

    def test_boot_start(self):
        calls = []
        names = [self.fake('devices', calls)]
        out = io.StringIO()
        with mock.patch.dict('os.environ', {'RELUME_DATA': str(self.data)}), contextlib.redirect_stdout(out), \
                mock.patch.object(cli.jobs, 'start', lambda: calls.append('jobs.start')), \
                mock.patch.object(cli.jobs, 'recover', lambda: calls.append('jobs.recover')), \
                mock.patch.object(cli.scheduler, 'start', lambda: calls.append('scheduler.start')):
            cli.boot(start=True, names=names)
        self.assertEqual(calls, ['devices.register', 'jobs.recover', 'jobs.start', 'scheduler.start', 'devices.start'])
        self.assertNotIn('password', out.getvalue())
        self.assertIsNone(db.conn().execute("SELECT 1 FROM settings WHERE key LIKE 'admin_%'").fetchone())

    def test_boot_applies_env_admin_password(self):
        self.enterContext(mock.patch.object(auth, 'SCRYPT_N', 2 ** 4))   # fast hashing, the KDF cost is not under test
        settings.put('admin_username', 'root')
        auth.set_password('old-password-1')
        db.conn().execute("INSERT INTO sessions VALUES ('t','2999-01-01T00:00:00Z')")
        db.conn().execute("INSERT INTO devices(name, kind, token_hash) VALUES ('Pixel', 'app', 'h')")
        out = io.StringIO()
        env = {'RELUME_DATA': str(self.data), auth.ADMIN_PASSWORD_ENV: 'brand-new-password'}
        with mock.patch.dict('os.environ', env), contextlib.redirect_stdout(out), \
                mock.patch.object(cli.jobs, 'start'), mock.patch.object(cli.jobs, 'recover'), \
                mock.patch.object(cli.scheduler, 'start'), mock.patch.object(cli.settings, 'check_token_file'):
            cli.boot(start=True, names=[])
        stored = settings.get('admin_password_hash')
        self.assertTrue(auth.verify_password('brand-new-password', stored))
        self.assertFalse(auth.verify_password('old-password-1', stored))
        self.assertEqual(db.conn().execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
        self.assertEqual(db.conn().execute('SELECT token_hash FROM devices').fetchone()[0], 'h')
        self.assertNotIn('brand-new-password', out.getvalue())

    def test_env_admin_password_without_account_or_too_short(self):
        with mock.patch.dict('os.environ', {auth.ADMIN_PASSWORD_ENV: 'brand-new-password'}), contextlib.redirect_stdout(io.StringIO()):
            auth.apply_env_password()
        self.assertIsNone(settings.get('admin_password_hash'))
        with mock.patch.dict('os.environ', {auth.ADMIN_PASSWORD_ENV: 'short'}), self.assertRaisesRegex(ValueError, 'at least 10'):
            auth.apply_env_password()

    def test_unknown_command(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(['bogus']), 2)
        self.assertIn('usage', err.getvalue())


class EmptyVolumeTest(DataTestCase):
    def test_boot_creates_dirs_with_modes(self):
        fresh = self.data / 'vol'          # does not exist: an empty, unprepared data volume
        with mock.patch.dict('os.environ', {'RELUME_DATA': str(fresh)}):
            cli.boot(names=[])
        for d in db.SUBDIRS + ('secrets',):
            self.assertTrue((fresh / d).is_dir(), d)
        modes = {d: (fresh / d).stat().st_mode & 0o777 for d in ('secrets', 'home', 'work', 'apk-cache')}
        self.assertEqual(modes, {'secrets': 0o700, 'home': 0o700, 'work': 0o770, 'apk-cache': 0o770})
        self.assertTrue((fresh / 'webui.db').is_file())

    def test_foreign_owned_dir_keeps_mode(self):
        """Root server, data/work made by the host user (uid 1001, mode 0755). boot must not
        tighten it, or the runner (uid 10001, gid 0) cannot enter it."""
        if os.geteuid() != 0:
            self.skipTest('chown and setpriv need root (T_PY runs as root)')
        os.chmod(self.data, 0o755)
        work = self.data / 'work'
        work.chmod(0o755)
        os.chown(work, 1001, 1001)
        with mock.patch.dict('os.environ', {'RELUME_DATA': str(self.data)}):
            cli.boot(names=[])
        self.assertEqual(work.stat().st_mode & 0o777, 0o755)
        r = subprocess.run(['setpriv', '--reuid=10001', f'--regid={os.getgid()}', '--clear-groups',
                            'sh', '-c', f'cd {work} && echo ENTER'], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), 'ENTER')


class ImportCycleTest(unittest.TestCase):
    def test_each_module_imports_in_a_fresh_interpreter(self):
        """A circular named import only fails for some import orders, so import every module first in its own process."""
        pkg = pathlib.Path(__file__).resolve().parent.parent
        stems = [f.stem for f in sorted(pkg.glob('*.py')) if f.stem != '__init__']
        run = lambda stem: subprocess.run([sys.executable, '-c', f'import webui.{stem}'], cwd=pkg.parent,
                                          capture_output=True, text=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(stems)) as pool:
            results = list(pool.map(run, stems))
        for stem, r in zip(stems, results):
            with self.subTest(module=stem):
                self.assertEqual(r.returncode, 0, r.stderr)
