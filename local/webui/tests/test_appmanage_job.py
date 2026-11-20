import http.server
import threading
from unittest import mock

from webui import appmanage_job, appmanage_play, appmanage_store as store, db, devices, jobs
from webui.tests import device_helpers as dh
from webui.tests.helpers import DataTestCase

ADDR = '192.168.1.20:37011'
ONLINE = f'List of devices attached\n{ADDR}\tdevice\n'
PKG = 'com.example.app'
PM_LIST = f'package:{PKG} versionCode:7\n'
BODIES = {'/base': b'AAA', '/split': b'BBBB'}


class Source(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = BODIES.get(self.path.split('?')[0])
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class JobTest(DataTestCase):
    @classmethod
    def setUpClass(cls):   # one in-process source for the module: shutdown() waits one poll interval, 10 ms here instead of the 0.5 s default
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Source)
        threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
        cls.addClassCleanup(srv.server_close)
        cls.addClassCleanup(srv.shutdown)
        cls.base = f'http://127.0.0.1:{srv.server_address[1]}'

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(jobs.OPTION_DEFAULTS, {'appmanage': {}}))
        self.fa = dh.FakeAdb(self)
        self.log = dh.FakeLog()
        self.dev = dh.add_device()

    def online(self):
        self.fa.rule('devices', out=ONLINE)
        self.fa.rule('pm', out=PM_LIST)

    def action(self, action, source=None):
        aid = store.create_actions(PKG, action, [self.dev], source)[0]['id']
        return aid, self.run_job({'action_id': aid})

    def run_job(self, target):
        jid = dh.add_job(kind='appmanage', target=target)
        self.job = dh.job(jid, target=target, kind='appmanage')
        appmanage_job.handle(self.job, self.log)
        return jid

    def row(self, aid):
        return dict(db.conn().execute('SELECT * FROM device_actions WHERE id=?', (aid,)).fetchone())

    def adb_calls(self, *tokens):
        return [c for c in self.fa.calls() if all(t in c for t in tokens)]

    def play(self, urls):
        apks = [{'name': n, 'url': f'{self.base}{p}?token=SECRET'} for n, p in urls]
        return mock.patch.object(appmanage_play, 'resolve', return_value={'version_code': 9, 'apks': apks})

    def pipe(self):
        sent = []

        def fake(args, src, **kw):
            sent.append((args, src.read()))
            return 0, 'Success\n'
        return sent, mock.patch.object(devices, 'adb_pipe', side_effect=fake)

    def test_remove_done_and_inventory_reread(self):
        self.online()
        self.fa.rule('uninstall', out='Success\n')
        aid, _ = self.action('remove')
        self.assertEqual(self.row(aid)['state'], 'done')
        self.assertEqual(self.adb_calls('uninstall', PKG)[0][-2:], ['uninstall', PKG])
        self.assertEqual(set(store.inventory(self.dev)[self.dev]), {PKG})

    def test_remove_failure_is_recorded(self):
        self.online()
        self.fa.rule('uninstall', out='Failure [DELETE_FAILED_INTERNAL_ERROR]\n')
        aid, _ = self.action('remove')
        r = self.row(aid)
        self.assertEqual((r['state'], r['error_code']), ('failed', 'UNINSTALL_FAILED'))
        self.assertIn(f'for {PKG}', r['error_text'])

    def test_offline_parks_the_action(self):
        aid, jid = self.action('remove')
        r = self.row(aid)
        self.assertEqual((r['state'], r['job_id']), ('queued', None))
        self.assertEqual(db.conn().execute('SELECT reason FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'DEVICE_OFFLINE')
        self.assertEqual(self.adb_calls('uninstall'), [])

    def test_cancelled_action_is_a_noop(self):
        self.online()
        aid = store.create_actions(PKG, 'remove', [self.dev])[0]['id']
        store.cancel(aid)
        self.run_job({'action_id': aid})
        self.assertEqual(self.row(aid)['state'], 'cancelled')
        self.assertEqual(self.fa.calls(), [])

    def test_refresh_ingests_and_requeues_parked_actions(self):
        self.online()
        aid = store.create_actions(PKG, 'remove', [self.dev])[0]['id']
        self.run_job({'device_id': self.dev, 'refresh': True})
        self.assertEqual(list(store.inventory(self.dev)[self.dev]), [PKG])
        queued = db.conn().execute("SELECT target FROM jobs WHERE kind='appmanage' AND status='queued'").fetchall()
        self.assertIn(dh.canon({'action_id': aid}), [r[0] for r in queued])

    def test_refresh_offline_fails_the_job(self):
        with self.assertRaises(jobs.JobFailed) as cm:
            self.run_job({'device_id': self.dev, 'refresh': True})
        self.assertEqual(cm.exception.reason, 'DEVICE_OFFLINE')

    def test_play_direct_streams_every_apk_into_one_session(self):
        self.fa.rule('install-create', out='Success: created install session [42]\n')   # before 'pm' in online()
        self.fa.rule('install-commit', out='Success\n')
        self.online()
        sent, patch = self.pipe()
        with self.play([('base', '/base'), ('config.en', '/split')]), patch:
            aid, _ = self.action('install', 'play_direct')
        self.assertEqual(self.row(aid)['state'], 'done')
        self.assertEqual([(a[-5:], b) for a, b in sent],
                         [(['-S', '3', '42', 'base', '-'], b'AAA'), (['-S', '4', '42', 'config.en', '-'], b'BBBB')])
        self.assertEqual(len(self.adb_calls('install-commit', '42')), 1)
        self.assertEqual(self.adb_calls('install-abandon'), [])
        self.assertNotIn('token=', '\n'.join(self.log.lines))

    def test_play_direct_download_failure_abandons(self):
        self.fa.rule('install-create', out='Success: created install session [42]\n')
        self.online()
        sent, patch = self.pipe()
        with self.play([('base', '/base'), ('config.en', '/missing')]), patch:
            aid, _ = self.action('install', 'play_direct')
        r = self.row(aid)
        self.assertEqual((r['state'], r['error_code']), ('failed', 'PLAY_DOWNLOAD_FAILED'))
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(self.adb_calls('install-abandon', '42')), 1)
        self.assertEqual(self.adb_calls('install-commit'), [])
        self.assertNotIn('token=', r['error_text'] + '\n'.join(self.log.lines))

    def test_play_lookup_error_code_is_kept(self):
        self.online()
        err = appmanage_play.PlayLookupError('NO_PLAY_PROFILE', 'no profile')
        with mock.patch.object(appmanage_play, 'resolve', side_effect=err):
            aid, _ = self.action('install', 'play_direct')
        self.assertEqual(self.row(aid)['error_code'], 'NO_PLAY_PROFILE')
        self.assertEqual(self.adb_calls('install-create'), [])

    def test_play_page_opens_the_market_url(self):
        self.online()
        self.fa.rule('am', out='Starting: Intent\n')
        aid, _ = self.action('install', 'play_page')
        self.assertEqual(self.row(aid)['state'], 'done')
        self.assertIn(f"'market://details?id={PKG}'", self.adb_calls('am', 'start')[0])

    def test_detail_returns_the_action_of_the_job(self):
        self.online()
        self.fa.rule('uninstall', out='Success\n')
        aid, jid = self.action('remove')
        self.assertEqual(appmanage_job.detail(jid)['action']['id'], aid)
        self.assertIsNone(appmanage_job.detail(jid + 99)['action'])
