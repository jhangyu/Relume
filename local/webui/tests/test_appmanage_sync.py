"""App Manage over the companion sync (plan 2026-11-20 §1.4, §1.10, amendment A2): inventory ingest, action pickup,
install receipts finishing build actions, adopt, per-device locale."""
from unittest import mock

from webui import appmanage_store as store, db, jobs, ota, projects
from webui.server import HttpError
from webui.tests import device_helpers as dh
from webui.tests.helpers import DataTestCase, fake_request

UID = 'a1b2c3d4e5f60718'
GMS = 'app.revanced.android.gms'


def body(inventory=None, **extra):
    b = {'device': {'device_uid': UID, 'model': 'Pixel 9', 'sdk': 36, 'android_release': '16',
                    'abi_list': ['arm64-v8a'], 'app_version_code': 7}, 'packages': [], **extra}
    if inventory is not None:
        b['inventory'] = inventory
    return b


def inv(apps, full=True, base_rev=None, removed=()):
    return {'full': full, 'base_rev': base_rev, 'removed': list(removed),
            'apps': [{'package': p, 'label': p, 'version_code': 1, 'version_name': '1', 'app_bytes': 1, 'data_bytes': 2,
                      'cache_bytes': 3, 'size_reason': None, 'icon_sha256': None} for p in apps]}


class SyncTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(store, 'ICON_DIR', self.data / 'icons' / 'appmanage'))
        self.enterContext(mock.patch.dict(jobs.OPTION_DEFAULTS, {'appmanage': {}}))
        self.me = dh.add_app_device(token_hash='11' * 32, uid=UID)

    def sync(self, b, device_id=None):
        st, out = ota.sync(fake_request(json=b, session=None, device_id=device_id or self.me))
        self.assertEqual(st, 200)
        return out

    def locale(self, device_id=None):
        return db.conn().execute('SELECT locale FROM devices WHERE id=?', (device_id or self.me,)).fetchone()[0]

    def state(self, aid):
        return tuple(db.conn().execute('SELECT state, error_code FROM device_actions WHERE id=?', (aid,)).fetchone())

    def test_old_app_body_ingests_and_picks_up_nothing(self):
        aid = store.create_actions('a.b', 'install', [self.me], source='play_direct')[0]['id']
        out = self.sync(body())
        self.assertEqual((out['inventory'], out['actions']), (None, []))
        self.assertEqual(self.state(aid), ('queued', None))
        self.assertEqual((store.inventory(self.me), store.reports()), ({self.me: {}}, {}))
        self.assertIn('plan', out)

    def test_full_then_diff_then_stale(self):
        out = self.sync(body(inv(['a.x', 'a.y'])))
        self.assertEqual(out['inventory'], {'rev': 1, 'accepted': True, 'need_icons': []})
        out = self.sync(body(inv(['a.z'], full=False, base_rev=1, removed=['a.x'])))
        self.assertEqual(out['inventory']['rev'], 2)
        self.assertEqual(sorted(store.inventory(self.me)[self.me]), ['a.y', 'a.z'])
        out = self.sync(body(inv(['a.q'], full=False, base_rev=1)))
        self.assertEqual(out['inventory'], {'rev': 2, 'accepted': False, 'need_icons': []})
        self.assertEqual(sorted(store.inventory(self.me)[self.me]), ['a.y', 'a.z'])

    def test_pickup_only_once(self):
        aid = store.create_actions('a.b', 'remove', [self.me])[0]['id']
        out = self.sync(body(inv([])))
        self.assertEqual(out['actions'], [{'id': aid, 'package': 'a.b', 'action': 'remove', 'source': None, 'ref': None}])
        self.assertEqual(self.sync(body(inv([], full=False, base_rev=1)))['actions'], [])
        self.assertEqual(self.state(aid), ('delivered', None))

    def test_legacy_row_ignores_inventory_and_locale(self):
        with db.tx() as c:
            legacy = c.execute("INSERT INTO devices(name,kind,token_hash) VALUES ('Old','app',?)", ('22' * 32,)).lastrowid
        b = body(inv(['a.x']), locale='zh-TW')
        del b['device']['device_uid']
        out = self.sync(b, device_id=legacy)
        self.assertEqual((out['inventory'], out['actions']), (None, []))
        self.assertEqual((store.inventory(legacy), self.locale(legacy)), ({legacy: {}}, None))

    def test_locale_stored_kept_and_bad_shape_400(self):
        self.sync(body(locale='zh-TW'))
        self.assertEqual(self.locale(), 'zh-TW')
        self.sync(body())                                          # absent: the stored locale stays
        self.assertEqual(self.locale(), 'zh-TW')
        for tag in ('sr-Latn-RS', 'fil'):
            self.sync(body(locale=tag))
            self.assertEqual(self.locale(), tag)
        for bad in ('zh_TW', 'ZH-tw', 'en-', 7, ''):
            with self.assertRaises(HttpError) as cm:
                self.sync(body(locale=bad))
            self.assertEqual(cm.exception.status, 400)
            self.assertTrue(cm.exception.message.startswith('locale must be a BCP-47 tag'), cm.exception.message)
        self.assertEqual(self.locale(), 'fil')

    def test_receipt_finishes_build_action(self):
        dh.add_project(2, GMS)
        dh.assign_projects(self.me, 2)
        info = dh.pinfo(2, GMS, tag='7.2.1', path='/u/7.2.1/microg.apk')
        self.enterContext(mock.patch.object(projects, 'project_info', lambda pid, abis: info if pid == 2 else None))
        aid = store.create_actions(GMS, 'install', [self.me])[0]['id']
        self.assertEqual(self.sync(body(inv([])))['actions'][0]['ref'], {'kind': 'project', 'id': 2})
        st, _ = ota.post_install(fake_request(json={'kind': 'project', 'ref_id': 2, 'tag': '7.2.1', 'result': 'installed'},
                                              session=None, device_id=self.me))
        self.assertEqual((st, self.state(aid)), (200, ('done', None)))

    def test_adopt_moves_rows_and_cancels_adb_actions(self):
        adb = dh.add_device()
        store.ingest_full(adb, {'a.adb': {'label': None, 'version_code': 1, 'version_name': None, 'app_bytes': None,
                                          'data_bytes': None, 'cache_bytes': None, 'size_reason': 'x', 'icon_sha256': None}})
        theirs = store.create_actions('a.b', 'install', [adb], source='play_page')[0]['id']
        mine = store.create_actions('a.b', 'install', [self.me], source='play_direct')[0]['id']
        self.sync(body(inv(['a.phone']), locale='zh-TW'))
        st, _ = ota.adopt(fake_request(json={'device_id': adb}, session=None, device_id=self.me))
        self.assertEqual(st, 200)
        self.assertEqual(list(store.inventory(adb)[adb]), ['a.phone'])
        self.assertEqual(self.state(theirs), ('cancelled', 'DEVICE_ADOPTED'))
        self.assertEqual(self.state(mine), ('delivered', None))
        self.assertEqual({a['device_id'] for a in store.actions()}, {adb})
        self.assertEqual(self.locale(adb), 'zh-TW')
