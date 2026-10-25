"""Relume app endpoints in ota.py (spec android 5, 5.1, 6.3, 6.4); token routing itself is tested in test_core_auth."""
from unittest import mock

from webui import build, db, ota, projects
from webui.server import HttpError
from webui.tests import helpers
from webui.tests.device_helpers import (add_app_device, add_catalog_item, add_device, add_job, add_project, assign,
                                        assign_projects, pinfo)

YT, GMS = 'com.google.android.youtube', 'app.revanced.android.gms'
SIGNER = 'ab' * 32


def sync_body(packages, abi_list=('arm64-v8a',)):
    return {'device': {'model': 'Pixel 9', 'sdk': 36, 'android_release': '16', 'abi_list': list(abi_list),
                       'app_version_code': 7}, 'packages': packages}


def pkg(name, version_code=1, signer=SIGNER):
    return {'package': name, 'version_code': version_code, 'version_name': '1.0', 'signer_sha256': signer}


def art(job_id):
    return {'id': job_id * 10, 'job_id': job_id, 'item_id': 1, 'variant': 'arm64-v8a', 'abi': 'arm64-v8a', 'package': YT,
            'version_name': '19.47.53', 'version_code': 1547, 'patches_version': 'v1', 'signer_sha256': SIGNER,
            'path': '/a/yt.apk', 'size': 3, 'sha256': 'cd' * 32, 'created_at': '2026-10-25T04:00:00Z'}


class AppTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        add_catalog_item(1, YT)
        self.me = add_app_device(token_hash='11' * 32)

    def call(self, fn, body=None, query=None, device_id=None):
        return fn(helpers.fake_request(json=body, query=query, session=None, device_id=device_id or self.me))

    def stored(self, device_id=None):
        return {r['package']: r['version_code'] for r in db.conn().execute(
            'SELECT * FROM device_packages WHERE device_id = ?', (device_id or self.me,))}

    def test_sync_stores_tracked_only(self):
        add_project(1, GMS)
        st, body = self.call(ota.sync, sync_body([pkg(YT, 5), pkg(GMS, 9), pkg('com.android.settings', 35)]))
        self.assertEqual(st, 200)
        self.assertEqual(self.stored(), {YT: 5, GMS: 9})
        self.assertEqual(body['tracked_packages'], [GMS, YT])
        self.assertRegex(body['plan']['revision'], r'^[0-9a-f]{64}$')
        dev = body['device']
        self.assertEqual((dev['model'], dev['sdk'], dev['android_release'], dev['abi_list'], dev['app_version_code']),
                         ('Pixel 9', 36, '16', 'arm64-v8a', 7))
        self.assertIsNotNone(dev['last_seen_at'])
        self.call(ota.sync, sync_body([pkg(YT, 6)]))               # the report replaces, it does not merge
        self.assertEqual(self.stored(), {YT: 6})

    def test_sync_rejects_malformed_entry(self):
        self.call(ota.sync, sync_body([pkg(YT, 5)]))
        cases = (
            ([pkg(YT), {'package': GMS, 'version_code': 'x'}], "packages[1].version_code must be a non-negative integer, got 'x'"),
            ([pkg(YT, signer='AB' * 32)], 'packages[0].signer_sha256 must be 64 lowercase hex characters or null'),
            ([pkg('not a package')], "packages[0].package must be an Android package name, got 'not a package'"),
            ([pkg(YT), pkg(YT)], 'packages[1].package must be unique in packages'),
            (['x'], "packages[0] must be an object, got 'x'"),
        )
        for packages, msg in cases:
            with self.assertRaises(HttpError, msg=msg) as cm:
                self.call(ota.sync, sync_body(packages))
            self.assertEqual(cm.exception.status, 400)
            self.assertTrue(cm.exception.message.startswith(msg), cm.exception.message)
        with self.assertRaises(HttpError) as cm:
            self.call(ota.sync, sync_body([pkg(YT, 9)], abi_list=['mips']))
        self.assertTrue(cm.exception.message.startswith('device.abi_list[0] must be one of'), cm.exception.message)
        self.assertEqual(self.stored(), {YT: 5})                    # a rejected sync writes nothing

    def test_adopt_keeps_history_and_one_row(self):
        add_project(1, GMS)
        adb = add_device(after_build=1)
        assign(adb, 1)
        assign_projects(adb, 1)
        jid = add_job(target={'device_id': adb}, status='success')
        with db.tx() as c:
            for did, job in ((adb, jid), (self.me, None)):
                c.execute("INSERT INTO ota_installs(job_id,device_id,kind,ref_id,package,result) "
                          "VALUES (?,?,'item',1,?,'installed')", (job, did, YT))
        st, body = self.call(ota.adopt, {'device_id': adb})
        self.assertEqual((st, body['device']['id'], body['device']['kind'], body['device']['online']), (200, adb, 'app', None))
        rows = [dict(r) for r in db.conn().execute('SELECT * FROM devices')]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r['id'], r['kind'], r['serial'], r['host'], r['connect_port'], r['ota_after_build'],
                          r['token_hash'], r['model']), (adb, 'app', None, None, None, 0, '11' * 32, 'Pixel 9'))
        c = db.conn()
        self.assertEqual([x[0] for x in c.execute('SELECT item_id FROM device_assignments WHERE device_id = ?', (adb,))], [1])
        self.assertEqual(projects.assigned_projects(adb), [1])
        self.assertEqual([x[0] for x in c.execute('SELECT device_id FROM ota_installs ORDER BY id')], [adb, adb])
        other = add_app_device(token_hash='22' * 32)
        for body, status, msg in (({'device_id': other}, 409, 'not an ADB device'),
                                  ({'device_id': 999}, 404, 'device 999 not found'),
                                  ({'device_id': '1'}, 400, "device_id must be an integer, got '1'")):
            with self.assertRaises(HttpError) as cm:
                self.call(ota.adopt, body, device_id=adb)
            self.assertEqual((cm.exception.status, cm.exception.message), (status, msg))

    def test_install_receipt_stale_and_valid(self):
        assign(self.me, 1)
        add_project(2, GMS)
        info = pinfo(2, GMS, tag='7.2.1', path='/u/7.2.1/microg.apk')
        for mod, name, fn in ((build, 'latest_artifact', lambda i, abi: art(5)),
                              (projects, 'project_info', lambda pid, abis: info if pid == 2 else None)):
            self.enterContext(mock.patch.object(mod, name, side_effect=fn))
        self.assertEqual(self.call(ota.me_plan)[1]['plan']['steps'][0]['action'], 'install')
        stale = ({'kind': 'item', 'ref_id': 1, 'artifact_id': 40, 'result': 'installed'},
                 {'kind': 'project', 'ref_id': 2, 'tag': '7.2.0', 'result': 'installed'})
        for body in stale:
            with self.assertRaises(HttpError) as cm:
                self.call(ota.post_install, body)
            self.assertEqual((cm.exception.status, cm.exception.message), (409, 'stale plan'))
        for body, msg in (({'kind': 'companion', 'ref_id': 9, 'tag': 'v1', 'result': 'installed'},
                           "kind must be item or project, got 'companion'"),
                          ({'kind': 'item', 'ref_id': 1, 'artifact_id': 50, 'result': 'failed', 'error_code': 'NOPE'},
                           'error_code must be one of')):
            with self.assertRaises(HttpError) as cm:
                self.call(ota.post_install, body)
            self.assertEqual(cm.exception.status, 400)
            self.assertTrue(cm.exception.message.startswith(msg), cm.exception.message)
        self.assertEqual(db.conn().execute('SELECT COUNT(*) FROM ota_installs').fetchone()[0], 0)
        st, body = self.call(ota.post_install, {'kind': 'item', 'ref_id': 1, 'artifact_id': 50, 'result': 'installed'})
        self.assertEqual((st, body), (200, {'ok': True}))
        self.assertEqual(self.call(ota.post_install, {'kind': 'project', 'ref_id': 2, 'tag': '7.2.1', 'result': 'failed',
                                                      'error_code': 'USER_REJECTED'})[0], 200)
        rows = [dict(r) for r in db.conn().execute('SELECT * FROM ota_installs ORDER BY id')]
        self.assertEqual([(r['job_id'], r['device_id'], r['kind'], r['build_job_id'], r['upstream_tag'], r['version_code'],
                           r['result'], r['error_code']) for r in rows],
                         [(None, self.me, 'item', 5, None, 1547, 'installed', None),
                          (None, self.me, 'project', None, '7.2.1', 255070218, 'failed', 'USER_REJECTED')])
        step = self.call(ota.me_plan)[1]['plan']['steps'][0]
        self.assertEqual((step['kind'], step['action'], step['download']), ('item', 'up_to_date', None))
