"""A companion up to v1.2.0 sends no device_uid: its sign-in, me, sync, plan and install receipt must keep working,
so it can upgrade itself (the followed Relume project) from inside the old app. Bodies are the v1.2.0 shapes
(android/.../api/Models.kt DeviceInfo, SyncReport, InstallReceipt)."""
import json
from unittest import mock

from webui import auth, db, projects
from webui.tests.device_helpers import add_project, assign_projects, pinfo
from webui.tests.helpers import DataTestCase, http, login, serve

SELF = 'io.github.jhangyu.relume'
OLD_DEVICE = {'name': 'Pixel 8', 'model': 'Pixel 8', 'sdk': 35, 'android_release': '15', 'abi_list': ['arm64-v8a']}


class LegacyClientTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(db.close)
        self.enterContext(mock.patch.object(auth, 'SCRYPT_N', 2 ** 4))
        self.base = serve(self, ['devices', 'notify', 'projects', 'ota'])
        login(self.base)

    def bearer(self, method, path, body=None):
        return http(method, self.base + path, body, headers={'Authorization': f'Bearer {self.token}'})

    def test_old_app_signs_in_and_upgrades_itself(self):
        st, _, raw = http('POST', self.base + '/api/auth/device',
                          {'username': 'admin', 'password': 'correct-horse-42', 'device': OLD_DEVICE})
        self.assertEqual(st, 200, raw)
        reg = json.loads(raw)
        self.token, did = reg['token'], reg['device']['id']
        self.assertEqual(db.conn().execute('SELECT device_uid FROM devices WHERE id=?', (did,)).fetchone()[0], None)

        self.assertEqual(self.bearer('GET', '/api/me')[0], 200)

        add_project(1, SELF)
        assign_projects(did, 1)
        info = pinfo(1, SELF, tag='1.3.0', path='/u/1.3.0/relume.apk')
        self.enterContext(mock.patch.object(projects, 'project_info', lambda pid, abis: info if pid == 1 else None))
        sync = {'device': {'model': 'Pixel 8', 'sdk': 35, 'android_release': '15', 'abi_list': ['arm64-v8a'],
                           'app_version_code': 12},
                'packages': [{'package': SELF, 'version_code': 12, 'version_name': '1.2.0', 'signer_sha256': 'ab' * 32}]}
        st, _, raw = self.bearer('POST', '/api/me/sync', sync)
        self.assertEqual(st, 200, raw)
        step = json.loads(raw)['plan']['steps'][0]
        self.assertEqual((step['kind'], step['action']), ('project', 'install'))

        st, _, raw = self.bearer('GET', '/api/me/plan?projects=1')
        self.assertEqual((st, json.loads(raw)['plan']['steps'][0]['action']), (200, 'install'))

        receipt = {'kind': 'project', 'ref_id': 1, 'result': 'installed', 'tag': '1.3.0'}   # old app omits null fields
        self.assertEqual(self.bearer('POST', '/api/me/installs', receipt)[0], 200)
        st, _, raw = self.bearer('GET', '/api/me/plan')
        self.assertEqual(json.loads(raw)['plan']['steps'][0]['action'], 'up_to_date')
        self.assertEqual(db.conn().execute('SELECT device_uid FROM devices WHERE id=?', (did,)).fetchone()[0], None)

    def test_present_but_bad_uid_still_400(self):
        for bad in (None, '', 'a/b', 7):
            st, _, _ = http('POST', self.base + '/api/auth/device', {
                'username': 'admin', 'password': 'correct-horse-42', 'device': {**OLD_DEVICE, 'device_uid': bad}})
            self.assertEqual(st, 400, bad)
