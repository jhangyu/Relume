from unittest import mock

from webui import db, events, jobs, ota
from webui.server import HttpError
from webui.tests import helpers
from webui.tests.device_helpers import (FakeAdb, FakeLog, add_catalog_item, add_device, add_job, add_project, assign,
                                        assign_projects, job, pinfo, req)

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
        self.infos = {1: pinfo(path=str(self.gms))}
        self.assigned = []
        add_catalog_item(1, YT)
        add_catalog_item(2, YTM)
        self.d = add_device()
        assign(self.d, 1, 2)
        self.arts = {1: art(1, 6, str(self.yt)), 2: art(2, 7, str(self.ytm))}
        self.deps = {1: [1]}
        for name, fn in (('_latest_artifact', lambda i, abi: self.arts.get(i)),
                         ('_item_projects', lambda i: self.deps.get(i, [])),
                         ('_assigned_projects', lambda d: list(self.assigned)),
                         ('_project_info', lambda pid, abis: self.infos.get(pid))):
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
        self.assertEqual(self.installs(jid), [('project', GMS, 'failed', 'INSUFFICIENT_STORAGE'),
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

    def test_missing_project_pulled_then_installed(self):
        self.infos[1] = pinfo(tag=None)
        pulled = []

        def fake_enqueue(kind, target, trigger, schedule_id=None, options=None):
            pulled.append((kind, target, trigger, options))
            self.infos[1] = pinfo(tag='7.2.2', path=str(self.gms))
            return add_job(kind='upstream', target=target, options=options, status='success')
        self.online()
        self.fa.rule('install', out='Success\n')
        with mock.patch.object(jobs, 'enqueue', side_effect=fake_enqueue):
            jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(pulled, [('upstream', {'upstream_id': 23}, 'manual', {'rebuild': False, 'notify': True})])
        self.assertEqual(self.adb_cmds('install')[0], ['install', '-r', str(self.gms)])
        self.assertEqual(self.installs(jid)[0], ('project', GMS, 'installed', None))
        tag = db.conn().execute("SELECT upstream_tag FROM ota_installs WHERE job_id=? AND kind='project'", (jid,))
        self.assertEqual(tag.fetchone()[0], '7.2.2')

    def test_project_still_missing_not_downloaded(self):
        self.infos[1] = pinfo(tag=None)
        self.online()
        with mock.patch.object(jobs, 'enqueue', side_effect=lambda *a, **k: add_job(kind='upstream', status='failed')):
            jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(self.installs(jid), [('project', GMS, 'failed', 'NOT_DOWNLOADED'),
                                              ('item', YT, 'failed', 'DEPENDENCY_FAILED')])
        self.assertEqual(err, 'failed')
        # pulled, but the new release changed signer: blocked
        with mock.patch.object(jobs, 'enqueue', side_effect=lambda *a, **k: self.infos.update(
                {1: pinfo(path=str(self.gms), signer_alert='cd' * 32)}) or add_job(kind='upstream', status='success')):
            self.infos[1] = pinfo(tag=None)
            jid, err = self.run_job(target={'device_id': self.d, 'item_ids': [1]})
        self.assertEqual(self.installs(jid)[0], ('project', GMS, 'failed', 'SIGNER_CHANGED'))
        self.assertEqual(self.adb_cmds('install'), [])

    def test_project_install_records_tag_and_version(self):
        self.deps, self.assigned = {}, [1]
        self.online(pm=f'package:{GMS}\n')
        self.fa.rule('install', out='Success\n')
        jid, err = self.run_job(target={'device_id': self.d, 'project_ids': [1]})
        self.assertIsNone(err)
        r = db.conn().execute('SELECT * FROM ota_installs WHERE job_id=?', (jid,)).fetchall()
        self.assertEqual([(x['kind'], x['ref_id'], x['upstream_tag'], x['package'], x['version_name'], x['version_code'],
                           x['build_job_id'], x['result']) for x in r],
                         [('project', 1, '7.2.1', GMS, '7.2.1', 255070218, None, 'installed')])
        # same tag installed, package present: up to date
        jid, err = self.run_job(target={'device_id': self.d, 'project_ids': [1]})
        self.assertEqual(self.installs(jid), [('project', GMS, 'up_to_date', None)])

    def test_signer_blocked_dependency_fails_item(self):
        self.infos[1] = pinfo(path=str(self.gms), signer_alert='cd' * 32)
        self.online()
        self.fa.rule('install', out='Success\n')
        jid, err = self.run_job()
        self.assertIsNone(err)
        self.assertEqual(self.installs(jid), [('project', GMS, 'failed', 'SIGNER_CHANGED'),
                                              ('item', YT, 'failed', 'DEPENDENCY_FAILED'),
                                              ('item', YTM, 'installed', None)])
        self.assertEqual(self.adb_cmds('install'), [['install', '-r', str(self.ytm)]])

    def project_devices(self):
        add_project(1, GMS, 23)
        d2 = add_device(serial='SER2', host='192.168.1.21')
        assign_projects(self.d, 1)
        assign_projects(d2, 1)
        return d2

    def test_install_route_default_devices(self):
        d2 = self.project_devices()
        st, body = ota.install_project(req(params={'id': 1}))
        self.assertEqual(st, 200)
        self.assertEqual([(jobs.get(j)['target'], jobs.get(j)['trigger']) for j in body['job_ids']],
                         [({'device_id': self.d, 'project_ids': [1]}, 'manual'),
                          ({'device_id': d2, 'project_ids': [1]}, 'manual')])
        st, body = ota.install_project(req(params={'id': 1}, body={'device_ids': [d2]}))
        self.assertEqual([jobs.get(j)['target'] for j in body['job_ids']], [{'device_id': d2, 'project_ids': [1]}])

    def test_install_route_409_on_alert(self):
        self.project_devices()
        self.infos[1] = pinfo(path=str(self.gms), signer_alert='cd' * 32)
        with self.assertRaises(HttpError) as cm:
            ota.install_project(req(params={'id': 1}))
        self.assertEqual((cm.exception.status, cm.exception.message),
                         (409, 'signer changed: confirm on the Followed projects page'))
        with self.assertRaises(HttpError) as cm:
            ota.install_project(req(params={'id': 9}))
        self.assertEqual((cm.exception.status, cm.exception.message), (404, 'project not found'))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM jobs WHERE kind='ota'").fetchone()[0], 0)

    def test_install_route_unassigned_400(self):
        add_project(1, GMS, 23)
        with self.assertRaises(HttpError) as cm:
            ota.install_project(req(params={'id': 1}))
        self.assertEqual((cm.exception.status, cm.exception.message), (400, 'project not assigned to any phone'))
        d3 = add_device(serial='SER3', host='192.168.1.22')
        assign_projects(self.d, 1)
        for ids in ([d3], [self.d, d3]):
            with self.assertRaises(HttpError) as cm:
                ota.install_project(req(params={'id': 1}, body={'device_ids': ids}))
            self.assertEqual((cm.exception.status, cm.exception.message), (400, 'device not assigned'))
        for bad in ([], 'x', [True]):
            with self.assertRaises(HttpError) as cm:
                ota.install_project(req(params={'id': 1}, body={'device_ids': bad}))
            self.assertEqual(cm.exception.status, 400)

    def test_recovery_finds_project_package(self):
        self.online()
        self.fa.rule('install', out='Success\n')
        jid, err = self.run_job(options={'mode': 'force_reinstall', 'package': GMS})
        self.assertIsNone(err)
        cmds = [c[2:] for c in self.fa.calls() if len(c) > 2 and c[2] in ('uninstall', 'install')]
        self.assertEqual(cmds, [['uninstall', GMS], ['install', '-r', str(self.gms)]])
        self.assertEqual(self.installs(jid), [('project', GMS, 'installed', None)])
        # directly assigned project, no matching ABI: a failed step, no adb install
        self.deps, self.assigned = {}, [2]
        self.infos[2] = pinfo(2, 'org.example.reader', path=None)
        jid, err = self.run_job(options={'mode': 'downgrade', 'package': 'org.example.reader'})
        self.assertEqual(self.installs(jid), [('project', 'org.example.reader', 'failed', 'NO_MATCHING_ABI')])
        self.assertEqual(len(self.adb_cmds('install')), 1)

    def test_ota_now_project_ids(self):
        self.assigned = [1]
        st, body = ota.ota_now(req(params={'id': self.d}, body={'project_ids': [1]}))
        self.assertEqual(jobs.get(body['job_id'])['target'], {'device_id': self.d, 'project_ids': [1]})
        st, body = ota.ota_now(req(params={'id': self.d}, body={'item_ids': [2], 'project_ids': [1]}))
        self.assertEqual(jobs.get(body['job_id'])['target'], {'device_id': self.d, 'item_ids': [2], 'project_ids': [1]})
        for bad, msg in (([5], 'project not assigned to this device'), ([], 'project_ids must be a non-empty list of integers'),
                         ([True], 'project_ids must be a non-empty list of integers')):
            with self.assertRaises(HttpError) as cm:
                ota.ota_now(req(params={'id': self.d}, body={'project_ids': bad}))
            self.assertEqual((cm.exception.status, cm.exception.message), (400, msg))

    def test_auto_push_target_runs_project_only(self):
        self.assigned = [1]
        self.online()
        self.fa.rule('install', out='Success\n')
        target = {'device_id': self.d, 'project_ids': [1]}
        jid, err = self.run_job(target=target, options={'notify': False}, trigger='upstream')
        self.assertIsNone(err)
        self.assertEqual(self.adb_cmds('install'), [['install', '-r', str(self.gms)]])
        self.assertEqual(self.installs(jid), [('project', GMS, 'installed', None)])
        d = ota.detail(jid)
        self.assertEqual((d['item_ids'], d['project_ids'], d['outcome']), ([], [1], 'done'))
        # no scope keys: every assigned item and project
        jid, err = self.run_job()
        self.assertIsNone(ota.detail(jid)['project_ids'])

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
