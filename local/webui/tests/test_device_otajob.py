from unittest import mock

from webui import db, events, jobs, ota
from webui.server import HttpError
from webui.tests import helpers
from webui.tests.device_helpers import (FakeAdb, FakeLog, add_catalog_item, add_device, add_job, assign, job, req)

YT, YTM, GMS = 'com.google.android.youtube', 'com.google.android.apps.youtube.music', 'app.revanced.android.gms'
ADDR = '192.168.1.20:37011'
ONLINE = f'List of devices attached\n{ADDR}\tdevice\n'


def art(item_id, job_id, path):
    return {'id': job_id * 10, 'job_id': job_id, 'item_id': item_id, 'variant': 'arm64-v8a', 'abi': 'arm64-v8a',
            'package': YT, 'version_name': '19.47.53', 'version_code': 1, 'patches_version': 'v1',
            'signer_sha256': 'aa', 'path': path, 'size': 3, 'sha256': 'bb', 'created_at': '2026-10-19T04:00:00Z'}


class OtaJobTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.fa = FakeAdb(self)
        self.ev = []
        for name in ('ota.done', 'ota.failed', 'device.offline'):
            events.subscribe(name, lambda p, n=name: self.ev.append((n, p)))
        self.yt, self.ytm, self.gms = (db.DATA / 'yt.apk', db.DATA / 'ytm.apk', db.DATA / 'microg.apk')
        for f in (self.yt, self.ytm, self.gms):
            f.write_bytes(b'apk')
        self.microg = {'dependency_app_id': 1, 'upstream_id': 7, 'package': GMS, 'display_name': 'MicroG RE',
                       'tag': 'v7.2.1', 'path': str(self.gms), 'signer_sha256': None}
        add_catalog_item(1, YT)
        add_catalog_item(2, YTM)
        self.d = add_device()
        assign(self.d, 1, 2)
        self.arts = {1: art(1, 6, str(self.yt)), 2: art(2, 7, str(self.ytm))}
        self.deps = {1: [self.microg]}
        for name, fn in (('_latest_artifact', lambda i, abi: self.arts.get(i)),
                         ('_item_dependencies', lambda i: self.deps.get(i, []))):
            p = mock.patch.object(ota, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (('RETRY_WAIT', 0), ('INSTALL_TIMEOUT', 900)):
            self.addCleanup(setattr, ota, name, getattr(ota, name))
            setattr(ota, name, value)

    def online(self, pm='package:com.android.settings\n'):
        self.fa.rule('devices', out=ONLINE)
        self.fa.rule('pm', out=pm)

    def run_job(self, target=None, options=None, trigger='manual'):
        target = target or {'device_id': self.d}
        jid = add_job(target=target, options=options, trigger=trigger)
        try:
            ota.handle(job(jid, target, options, trigger), FakeLog())
            return jid, None
        except jobs.JobFailed as e:
            return jid, e.reason

    def installs(self, jid):
        return [(r['kind'], r['package'], r['result'], r['error_code']) for r in
                db.conn().execute('SELECT * FROM ota_installs WHERE job_id=? ORDER BY id', (jid,))]

    def adb_cmds(self, verb):
        return [c[2:] for c in self.fa.calls() if len(c) > 2 and c[2] == verb]

    def outcome(self, jid):
        r = db.conn().execute('SELECT o.outcome, j.reason FROM ota_jobs o JOIN jobs j ON j.id=o.job_id WHERE o.job_id=?',
                              (jid,)).fetchone()
        return r['outcome'], r['reason']

    def test_ota_installs_in_order_with_dependency(self):
        self.online()
        self.fa.rule('install', out='Performing Streamed Install\nSuccess\n')
        jid, err = self.run_job()
        self.assertIsNone(err)
        self.assertEqual(self.adb_cmds('install'), [['install', '-r', str(self.gms)], ['install', '-r', str(self.yt)],
                                                    ['install', '-r', str(self.ytm)]])
        self.assertEqual([i[2] for i in self.installs(jid)], ['installed'] * 3)
        self.assertEqual(self.outcome(jid), ('done', 'done'))
        self.assertEqual(self.ev, [('ota.done', {'job_id': jid, 'device_id': self.d, 'outcome': 'done'})])

    def test_dependency_failure_marks_items(self):
        self.online()
        self.fa.rule('install', str(self.gms), out='Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]\n', rc=1)
        self.fa.rule('install', out='Success\n')
        jid, err = self.run_job()
        self.assertIsNone(err)
        self.assertEqual(self.installs(jid), [('dependency', GMS, 'failed', 'INSUFFICIENT_STORAGE'),
                                              ('item', YT, 'failed', 'DEPENDENCY_FAILED'),
                                              ('item', YTM, 'installed', None)])
        self.assertNotIn(['install', '-r', str(self.yt)], self.adb_cmds('install'))
        self.assertEqual(self.outcome(jid), ('partial', 'partial'))
        self.assertEqual(self.ev[0][0], 'ota.done')

    def test_all_fail_outcome_failed(self):
        self.online()
        self.fa.rule('install', out='Failure [INSTALL_FAILED_OTHER]\n', rc=1)
        jid, err = self.run_job()
        self.assertEqual(err, 'failed')
        self.assertEqual(self.outcome(jid), ('failed', 'failed'))
        self.assertEqual(self.ev, [('ota.failed', {'job_id': jid, 'device_id': self.d, 'reason': 'failed'})])

    def test_manual_offline_fails_device_offline(self):
        jid, err = self.run_job()
        self.assertEqual(err, 'failed: DEVICE_OFFLINE')
        self.assertEqual(self.outcome(jid)[0], 'failed')
        self.assertEqual(self.ev, [('ota.failed', {'job_id': jid, 'device_id': self.d, 'reason': 'DEVICE_OFFLINE'})])

    def test_scheduled_offline_retries_then_skipped(self):
        jid, err = self.run_job(options={'retries': 1}, trigger='schedule')
        self.assertEqual(err, 'skipped_offline')
        self.assertEqual(len([c for c in self.fa.calls() if c[0] == 'connect']), 2)
        self.assertEqual(self.outcome(jid), ('skipped_offline', 'skipped_offline'))
        self.assertEqual(self.ev, [('device.offline', {'job_id': jid, 'device_id': self.d})])

    def test_unauthorized(self):
        self.fa.rule('devices', out=f'List of devices attached\n{ADDR}\tunauthorized\n')
        jid, err = self.run_job()
        self.assertEqual(err, 'failed: UNAUTHORIZED')

    def test_device_removed(self):
        jid, err = self.run_job(target={'device_id': 999})
        self.assertEqual(err, 'device removed')

    def test_recovery_needs_manual_trigger(self):
        jid, err = self.run_job(options={'mode': 'force_reinstall', 'package': YT}, trigger='schedule')
        self.assertEqual(err, 'recovery requires manual trigger')
        self.assertEqual(self.fa.calls(), [])

    def test_force_reinstall_argv(self):
        self.online()
        self.fa.rule('install', out='Success\n')
        jid, err = self.run_job(options={'mode': 'force_reinstall', 'package': YT})
        self.assertIsNone(err)
        cmds = [c[2:] for c in self.fa.calls() if len(c) > 2 and c[2] in ('uninstall', 'install')]
        self.assertEqual(cmds, [['uninstall', YT], ['install', '-r', str(self.yt)]])

    def test_downgrade_argv(self):
        self.online()
        self.fa.rule('install', out='Success\n')
        jid, err = self.run_job(options={'mode': 'downgrade', 'package': YTM})
        self.assertEqual(self.adb_cmds('install'), [['install', '-r', '-d', str(self.ytm)]])

    def test_package_not_assigned(self):
        jid, err = self.run_job(options={'mode': 'downgrade', 'package': 'x.y.z'})
        self.assertEqual(err, 'package not assigned')
        self.assertEqual(self.outcome(jid)[0], 'failed')

    def test_recovery_route_wrong_job_404(self):
        a, b = add_job(target={'device_id': self.d}, status='failed'), add_job(target={'device_id': self.d}, status='failed')
        with db.tx() as c:
            iid = c.execute("INSERT INTO ota_installs(job_id,device_id,kind,ref_id,package,result,error_code) "
                            "VALUES (?,?,'item',1,?,'failed','SIGNATURE_MISMATCH')", (a, self.d, YT)).lastrowid
        with self.assertRaises(HttpError) as cm:
            ota.force_reinstall(req(params={'job_id': b}, body={'install_id': iid}))
        self.assertEqual(cm.exception.status, 404)
        st, body = ota.force_reinstall(req(params={'job_id': a}, body={'install_id': iid}))
        row = jobs.get(body['job_id'])
        self.assertEqual((row['target'], row['options']['mode'], row['options']['package'], row['trigger']),
                         ({'device_id': self.d}, 'force_reinstall', YT, 'manual'))
        st, body = ota.downgrade(req(params={'job_id': a}, body={'install_id': iid}))
        self.assertEqual(jobs.get(body['job_id'])['options']['mode'], 'downgrade')

    def test_after_build_enqueues_only_opted_devices(self):
        d2 = add_device(serial='SER2', after_build=1)
        assign(d2, 1)
        ota.on_build_succeeded({'item_id': 1, 'job_id': 5, 'app_version': 'x', 'patches_version': 'v', 'artifacts': []})
        rows = db.conn().execute("SELECT target, trigger FROM jobs WHERE kind='ota'").fetchall()
        self.assertEqual([(r['target'], r['trigger']) for r in rows], [(f'{{"device_id":{d2},"item_ids":[1]}}', 'after_build')])

    def test_artifact_reresolved_once(self):
        self.deps = {}
        seq = [art(1, 6, str(self.yt)), art(1, 6, str(db.DATA / 'gone.apk')), art(1, 6, str(self.yt))]
        self.arts = {}
        with mock.patch.object(ota, '_latest_artifact', side_effect=seq) as la:
            self.online()
            self.fa.rule('install', out='Success\n')
            jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(la.call_count, 3)
        self.assertEqual(self.adb_cmds('install'), [['install', '-r', str(self.yt)]])

    def test_missing_dependency_pulled(self):
        self.microg['path'] = None
        pulled = []

        def fake_enqueue(kind, target, trigger, schedule_id=None, options=None):
            pulled.append((kind, target, trigger, options))
            self.microg['path'] = str(self.gms)
            return add_job(kind='upstream', target=target, options=options, status='success')
        self.online()
        self.fa.rule('install', out='Success\n')
        with mock.patch.object(jobs, 'enqueue', side_effect=fake_enqueue):
            jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(pulled, [('upstream', {'upstream_id': 7}, 'manual', {'rebuild': False, 'notify': True})])
        self.assertEqual(self.adb_cmds('install')[0], ['install', '-r', str(self.gms)])
        self.assertEqual(self.installs(jid)[0][2], 'installed')

    def test_missing_dependency_still_missing(self):
        self.microg['path'] = None
        self.online()
        with mock.patch.object(jobs, 'enqueue', side_effect=lambda *a, **k: add_job(kind='upstream', status='failed')):
            jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(self.installs(jid), [('dependency', GMS, 'failed', 'DEPENDENCY_FAILED'),
                                              ('item', YT, 'failed', 'DEPENDENCY_FAILED')])
        self.assertEqual(err, 'failed')

    def test_install_signature_mismatch_code(self):
        self.deps = {}
        self.online()
        self.fa.rule('install', out='Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]\n', rc=1)
        jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(self.installs(jid), [('item', YT, 'failed', 'SIGNATURE_MISMATCH')])

    def test_install_timeout_code(self):
        self.deps = {}
        ota.INSTALL_TIMEOUT = 1
        self.online()
        self.fa.rule('install', hang=True)
        jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(self.installs(jid), [('item', YT, 'failed', 'TIMEOUT')])

    def test_ota_now_route(self):
        add_catalog_item(3, 'a.b.c')
        with self.assertRaises(HttpError):
            ota.ota_now(req(params={'id': self.d}, body={'item_ids': [3]}))
        with self.assertRaises(HttpError) as cm:
            ota.ota_now(req(params={'id': 999}))
        self.assertEqual(cm.exception.status, 404)
        st, body = ota.ota_now(req(params={'id': self.d}, body={'item_ids': [2]}))
        self.assertEqual(jobs.get(body['job_id'])['target'], {'device_id': self.d, 'item_ids': [2]})
        st, body = ota.ota_now(req(params={'id': self.d}))
        self.assertEqual(jobs.get(body['job_id'])['target'], {'device_id': self.d})
