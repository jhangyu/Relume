import contextlib, io, os, subprocess, sys, threading, types
from unittest import mock
from webui import db, server
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
        p = mock.patch.dict(sys.modules, {'webui.' + name: m}); p.start(); self.addCleanup(p.stop)
        return name

    def test_boot_order_no_threads(self):
        calls = []
        names = [self.fake('catalog', calls), self.fake('build', calls)]
        before = threading.active_count()
        # patch.dict restores sys.modules afterwards; popping webui.ci makes the check
        # independent of whether an earlier test (test_ci) already imported it
        with mock.patch.dict('os.environ', {'RELUME_DATA': str(self.data)}), mock.patch.dict(sys.modules):
            sys.modules.pop('webui.ci', None)
            cli.boot(names=names)
            ci_imported = 'webui.ci' in sys.modules
        self.assertEqual(calls, ['catalog.seed_import', 'catalog.register', 'build.register'])
        self.assertEqual(threading.active_count(), before)
        self.assertFalse(ci_imported)

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
        self.assertIn('admin password: ', out.getvalue())

    def test_reset_password(self):
        db.conn().execute("INSERT INTO sessions VALUES ('t','2999-01-01T00:00:00Z')")
        out = io.StringIO()
        with mock.patch.object(server, 'MODULES', ()), mock.patch.dict('os.environ', {'RELUME_DATA': str(self.data)}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['reset-password']), 0)
        self.assertRegex(out.getvalue(), r'admin password: \S{20}\n')
        self.assertEqual(db.conn().execute('SELECT count(*) FROM sessions').fetchone()[0], 0)

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
        """CI case (review B1): ci-build runs as root, the workflow made data/work as the host user (uid 1001,
        mode 0755). boot must not tighten it, or the runner (uid 10001, gid 0) cannot enter it."""
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
