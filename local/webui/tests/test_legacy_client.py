"""A companion up to v1.2.0 sends no device_uid: it gets an install-only legacy row (hidden, no device settings).
Bodies are the v1.2.0 shapes (android/.../api/Models.kt DeviceInfo, SyncReport, InstallReceipt)."""
import json
from unittest import mock

from webui import auth, db, ota, projects
from webui.tests.device_helpers import add_app_device, add_catalog_item, add_project, assign_projects, pinfo
from webui.tests.helpers import DataTestCase, http, login, serve

SELF = 'io.github.jhangyu.relume'
OLD_DEVICE = {'name': 'Pixel 8', 'model': 'Pixel 8', 'sdk': 35, 'android_release': '15', 'abi_list': ['arm64-v8a']}
UID = 'a1b2c3d4e5f60718'
SYNC = {'device': {'model': 'Pixel 8', 'sdk': 35, 'android_release': '15', 'abi_list': ['arm64-v8a'],
                   'app_version_code': 12},
        'packages': [{'package': SELF, 'version_code': 12, 'version_name': '1.2.0', 'signer_sha256': 'ab' * 32}]}


def count(sql, *args):
    return db.conn().execute(sql, args).fetchone()[0]


class LegacyClientTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(db.close)
        self.base = serve(self, ['devices', 'notify', 'projects', 'ota'])
        self.cookie = login(self.base)
        add_project(1, SELF)
        add_project(2, 'com.other.app')
        infos = {1: pinfo(1, SELF, tag='1.3.0', path='/u/1.3.0/relume.apk'),
                 2: pinfo(2, 'com.other.app', tag='2.0', path='/u/2.0/other.apk')}
        self.enterContext(mock.patch.object(projects, 'project_info', lambda pid, abis: infos.get(pid)))
        add_catalog_item(7, 'com.unassigned.app')
        self.token, self.did = self.sign_in()

    def sign_in(self, device=OLD_DEVICE):
        st, _, raw = http('POST', self.base + '/api/auth/device',
                          {'username': 'admin', 'password': 'correct-horse-42', 'device': device})
        self.assertEqual(st, 200, raw)
        reg = json.loads(raw)
        self.assertEqual(reg['adb_devices'], [])
        return reg['token'], reg['device']['id']

    def bearer(self, method, path, body=None, token=None):
        return http(method, self.base + path, body, headers={'Authorization': f'Bearer {token or self.token}'})

    def steps(self, path):
        st, _, raw = self.bearer('GET', path)
        self.assertEqual(st, 200, raw)
        return json.loads(raw)['plan']['steps']

    def test_sign_in_makes_hidden_row(self):
        row = db.conn().execute('SELECT kind, legacy, device_uid FROM devices WHERE id=?', (self.did,)).fetchone()
        self.assertEqual(tuple(row), ('app', 1, None))
        normal = add_app_device(name='Mine')
        st, _, raw = http('GET', self.base + '/api/devices', headers={'Cookie': self.cookie})
        self.assertEqual((st, [d['id'] for d in json.loads(raw)['devices']]), (200, [normal]))

    def test_ota_plan_is_only_the_companion(self):
        st, _, raw = self.bearer('GET', '/api/me')
        tracked = json.loads(raw)['tracked_packages']
        self.assertEqual((st, tracked), (200, ota.tracked_packages()))   # S1: the full list, so the old app's cards are right
        self.assertGreater(len(tracked), 1)
        st, _, raw = self.bearer('POST', '/api/me/sync', SYNC)
        self.assertEqual(st, 200, raw)
        steps = json.loads(raw)['plan']['steps']
        self.assertEqual([(s['kind'], s['ref_id'], s['action']) for s in steps], [('project', 1, 'install')])
        self.assertEqual([(s['ref_id'], s['action']) for s in self.steps('/api/me/plan')], [(1, 'install')])

    def test_explicit_install_plan_ignores_assignment(self):
        st, _, raw = self.bearer('GET', '/api/me/plan?projects=2&items=7')
        self.assertEqual(st, 200, raw)
        got = {(s['kind'], s['ref_id']) for s in json.loads(raw)['plan']['steps']}
        self.assertEqual(got, {('project', 2), ('item', 7)})
        self.assertEqual(self.bearer('GET', '/api/me/plan?projects=99')[0], 200)

    def test_sync_writes_no_device_facts(self):
        facts = 'SELECT name, kind, legacy, device_uid, model, sdk, android_release, abi_list, app_version_code, token_hash ' \
                'FROM devices WHERE id=?'
        before = tuple(db.conn().execute(facts, (self.did,)).fetchone())
        self.assertEqual(self.bearer('POST', '/api/me/sync', SYNC)[0], 200)
        self.assertEqual(tuple(db.conn().execute(facts, (self.did,)).fetchone()), before)

    def test_legacy_token_only_reaches_the_old_app_routes(self):
        other = add_app_device(name='Other')
        assign_projects(other, 2)
        refused = (('GET', '/api/devices', None), ('GET', '/api/devices/discovered', None),
                   ('PATCH', f'/api/devices/{self.did}', {'name': 'x'}), ('POST', '/api/me/adopt', {'device_id': 1}),
                   ('PUT', f'/api/devices/{other}/assignments', {'item_ids': [], 'project_ids': []}),
                   ('PUT', '/api/projects/2', {'device_ids': []}), ('POST', '/api/projects/2/install', {}),
                   ('POST', '/api/ota/1/force-reinstall', {}), ('GET', '/api/schedules', None),
                   ('POST', '/api/schedules', {}), ('DELETE', '/api/schedules/1', None), ('GET', '/api/settings', None),
                   ('DELETE', '/api/projects/2', None))
        for method, path, body in refused:
            st, _, raw = self.bearer(method, path, body)
            self.assertEqual(st, 403, (method, path, raw))
        self.assertEqual(count('SELECT count(*) FROM device_projects WHERE device_id=?', other), 1)
        for method, path, body in (('GET', '/api/me', None), ('GET', '/api/me/plan', None),
                                   ('POST', '/api/me/sync', SYNC), ('GET', '/api/projects', None),
                                   ('PUT', f'/api/devices/{self.did}/assignments', {'item_ids': [], 'project_ids': [2]}),
                                   ('POST', '/api/me/installs', {'kind': 'project', 'ref_id': 2, 'result': 'installed',
                                                                 'tag': '2.0'})):
            st, _, raw = self.bearer(method, path, body)
            self.assertEqual(st, 200, (method, path, raw))

    def test_begin_install_sequence_of_v1_2_0(self):
        """AppModel.beginInstall: me -> PUT assignments (own id, app not assigned yet) -> plan(ids) -> receipt.
        The old app drops the whole install on any non-401 error of a step."""
        st, _, raw = self.bearer('GET', '/api/me')
        dev = json.loads(raw)['device']
        self.assertEqual((st, dev['assignments'], dev['id']), (200, [], self.did))
        st, _, raw = self.bearer('PUT', f'/api/devices/{self.did}/assignments', {'item_ids': [], 'project_ids': [2]})
        self.assertEqual(st, 200, raw)
        got = json.loads(raw)['device']
        self.assertEqual({k: got[k] for k in ('id', 'name', 'kind', 'model', 'sdk', 'android_release', 'app_version_code',
                                              'created_at', 'last_seen_at')},
                         {'id': self.did, 'name': 'Pixel 8', 'kind': 'app', 'model': 'Pixel 8', 'sdk': 35,
                          'android_release': '15', 'app_version_code': None, 'created_at': got['created_at'],
                          'last_seen_at': None})
        self.assertEqual(count('SELECT count(*) FROM device_projects'), 0)
        self.assertEqual([(s['ref_id'], s['action']) for s in self.steps('/api/me/plan?projects=2')], [(2, 'install')])
        receipt = {'kind': 'project', 'ref_id': 2, 'result': 'installed', 'tag': '2.0'}
        self.assertEqual(self.bearer('POST', '/api/me/installs', receipt)[0], 200)

    def test_receipt_accepted_not_recorded(self):
        for body in ({'kind': 'project', 'ref_id': 1, 'result': 'installed', 'tag': '1.3.0'},
                     {'kind': 'project', 'ref_id': 2, 'result': 'failed', 'tag': '2.0', 'error_code': 'USER_REJECTED'}):
            self.assertEqual(self.bearer('POST', '/api/me/installs', body)[0], 200)
        self.assertEqual(count('SELECT count(*) FROM ota_installs'), 0)

    def test_sync_with_uid_converts_row(self):
        body = json.loads(json.dumps(SYNC))
        body['device']['device_uid'] = UID
        self.assertEqual(self.bearer('POST', '/api/me/sync', body)[0], 200)
        row = db.conn().execute('SELECT legacy, device_uid, app_version_code FROM devices WHERE id=?',
                                (self.did,)).fetchone()
        self.assertEqual(tuple(row), (0, UID, 12))
        st, _, raw = http('GET', self.base + '/api/devices', headers={'Cookie': self.cookie})
        self.assertEqual([d['id'] for d in json.loads(raw)['devices']], [self.did])

    def test_sync_with_uid_merges_into_existing_row(self):
        owner = add_app_device(name='Mine', token_hash='11' * 32)
        with db.tx() as c:
            c.execute('UPDATE devices SET device_uid=? WHERE id=?', (UID, owner))
        assign_projects(owner, 2)
        body = json.loads(json.dumps(SYNC))
        body['device']['device_uid'] = UID
        st, _, raw = self.bearer('POST', '/api/me/sync', body)
        self.assertEqual(st, 200, raw)
        self.assertEqual(count('SELECT count(*) FROM devices WHERE id=?', self.did), 0)
        self.assertEqual(count('SELECT count(*) FROM device_projects WHERE device_id=?', owner), 1)
        self.assertEqual(self.bearer('GET', '/api/me')[0], 200)   # the old token now belongs to the owner row
        self.assertEqual(json.loads(self.bearer('GET', '/api/me')[2])['device']['id'], owner)

    def test_present_but_bad_uid_still_400(self):
        for bad in (None, '', 'a/b', 7):
            st, _, _ = http('POST', self.base + '/api/auth/device', {
                'username': 'admin', 'password': 'correct-horse-42', 'device': {**OLD_DEVICE, 'device_uid': bad}})
            self.assertEqual(st, 400, bad)

    def test_sync_stores_the_full_report_and_conversion_keeps_it(self):
        """S1: a legacy sync keeps every tracked package it reports, so explicit plans carry flags and the merge
        does not wipe the owner's report."""
        body = json.loads(json.dumps(SYNC))
        body['packages'].append({'package': 'com.other.app', 'version_code': 5, 'version_name': '1.0', 'signer_sha256': 'cd' * 32})
        self.assertEqual(self.bearer('POST', '/api/me/sync', body)[0], 200)
        self.assertEqual(count('SELECT count(*) FROM device_packages WHERE device_id=?', self.did), 2)
        body['device']['device_uid'] = UID
        self.assertEqual(self.bearer('POST', '/api/me/sync', body)[0], 200)
        self.assertEqual(count('SELECT count(*) FROM device_packages WHERE device_id=?', self.did), 2)

    def test_second_conversion_of_the_same_row_keeps_it(self):
        """S2: two concurrent syncs both saw legacy=1: the second must not delete the converted row."""
        for _ in range(2):
            with db.tx() as c:
                self.assertEqual(ota._leave_legacy(c, self.did, UID), self.did)
        self.assertEqual(count('SELECT count(*) FROM devices WHERE id=? AND legacy=0 AND device_uid=?', self.did, UID), 1)
        self.assertEqual(self.bearer('GET', '/api/me')[0], 200)

    def test_logout_deletes_the_legacy_row(self):
        self.assertEqual(self.bearer('POST', '/api/me/logout', {})[0], 200)
        self.assertEqual(count('SELECT count(*) FROM devices WHERE id=?', self.did), 0)

    def test_logout_keeps_a_normal_row(self):
        token = 'normal-token'
        did = add_app_device(name='Mine', token_hash=auth.token_hash(token))
        self.assertEqual(self.bearer('POST', '/api/me/logout', {}, token)[0], 200)
        self.assertEqual(count('SELECT count(*) FROM devices WHERE id=? AND token_hash IS NULL', did), 1)

    def test_legacy_sign_ins_list_and_revoke_are_session_only(self):
        normal = add_app_device(name='Mine', token_hash='22' * 32)
        st, _, raw = http('GET', self.base + '/api/legacy-sign-ins', headers={'Cookie': self.cookie})
        rows = json.loads(raw)['legacy_sign_ins']
        self.assertEqual((st, [r['id'] for r in rows]), (200, [self.did]))
        self.assertEqual(rows[0]['model'], 'Pixel 8')
        self.assertEqual(self.bearer('GET', '/api/legacy-sign-ins')[0], 403)
        self.assertEqual(self.bearer('DELETE', f'/api/legacy-sign-ins/{self.did}')[0], 403)
        self.assertEqual(http('DELETE', self.base + f'/api/legacy-sign-ins/{normal}', headers={'Cookie': self.cookie})[0], 404)
        self.assertEqual(http('DELETE', self.base + f'/api/legacy-sign-ins/{self.did}', headers={'Cookie': self.cookie})[0], 200)
        self.assertEqual(count('SELECT count(*) FROM devices WHERE id=?', self.did), 0)
        self.assertEqual(self.bearer('GET', '/api/me')[0], 401)

    def test_migration_marks_pre_uid_app_rows_legacy_and_conversion_restores_them(self):
        """B1: an app row from before device_uid (uid NULL, legacy 0) holds a v1.2.0 token: it becomes legacy,
        and the upgraded app's sync with a uid converts it back with its assignments."""
        did = add_app_device(name='Old', token_hash=auth.token_hash('old-token'))
        assign_projects(did, 2)
        db.conn().executescript(next(db.SCHEMA_DIR.glob('014_*.sql')).read_text())   # re-run 014 alone: later files need not be re-runnable
        self.assertEqual(count('SELECT legacy FROM devices WHERE id=?', did), 1)
        self.assertEqual(count('SELECT count(*) FROM devices WHERE id=? AND kind=?', self.did, 'app'), 1)
        body = json.loads(json.dumps(SYNC))
        body['device']['device_uid'] = UID
        self.assertEqual(self.bearer('POST', '/api/me/sync', body, 'old-token')[0], 200)
        row = db.conn().execute('SELECT legacy, device_uid FROM devices WHERE id=?', (did,)).fetchone()
        self.assertEqual(tuple(row), (0, UID))
        self.assertEqual(count('SELECT count(*) FROM device_projects WHERE device_id=?', did), 1)
