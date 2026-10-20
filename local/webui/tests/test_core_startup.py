import contextlib, io, sys, threading, types
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
        with mock.patch.dict('os.environ', {'RV_DATA': str(self.data)}), mock.patch.dict(sys.modules):
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
        with mock.patch.dict('os.environ', {'RV_DATA': str(self.data)}), contextlib.redirect_stdout(out), \
                mock.patch.object(cli.jobs, 'start', lambda: calls.append('jobs.start')), \
                mock.patch.object(cli.jobs, 'recover', lambda: calls.append('jobs.recover')), \
                mock.patch.object(cli.scheduler, 'start', lambda: calls.append('scheduler.start')):
            cli.boot(start=True, names=names)
        self.assertEqual(calls, ['devices.register', 'jobs.recover', 'jobs.start', 'scheduler.start', 'devices.start'])
        self.assertIn('admin password: ', out.getvalue())

    def test_reset_password(self):
        db.conn().execute("INSERT INTO sessions VALUES ('t','2999-01-01T00:00:00Z')")
        out = io.StringIO()
        with mock.patch.object(server, 'MODULES', ()), mock.patch.dict('os.environ', {'RV_DATA': str(self.data)}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['reset-password']), 0)
        self.assertRegex(out.getvalue(), r'admin password: \S{20}\n')
        self.assertEqual(db.conn().execute('SELECT count(*) FROM sessions').fetchone()[0], 0)

    def test_unknown_command(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(['bogus']), 2)
        self.assertIn('usage', err.getvalue())
