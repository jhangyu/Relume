import hashlib
import sqlite3
from unittest import mock

from webui import appmanage_store as store, db, jobs, settings
from webui.server import HttpError
from webui.tests import device_helpers as dh
from webui.tests.helpers import DataTestCase

PNG = b'\x89PNG\r\n\x1a\n' + b'icon'
PNG_SHA = hashlib.sha256(PNG).hexdigest()


def app(pkg, vc=1, icon=None, **kw):
    return {'package': pkg, 'label': pkg.upper(), 'version_code': vc, 'version_name': f'{vc}.0',
            'app_bytes': 1, 'data_bytes': 2, 'cache_bytes': 3, 'size_reason': None, 'icon_sha256': icon, **kw}


def inv(apps, full=True, base_rev=None, removed=()):
    return store.parse_inventory({'full': full, 'base_rev': base_rev, 'apps': apps, 'removed': list(removed)})


class StoreTest(DataTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(store, 'ICON_DIR', self.data / 'icons' / 'appmanage'))
        self.enterContext(mock.patch.dict(jobs.OPTION_DEFAULTS, {'appmanage': {}}))
        self.phone = dh.add_app_device('Phone')
        self.tab = dh.add_device()

    def assertHttp(self, status, fn, *a, **kw):
        with self.assertRaises(HttpError) as cm:
            fn(*a, **kw)
        self.assertEqual(cm.exception.status, status, cm.exception.message)
        return cm.exception.message

    def state(self, aid):
        return db.conn().execute('SELECT state, error_code, error_text FROM device_actions WHERE id=?', (aid,)).fetchone()

    # migration
    def test_migration_tables_and_job_kind(self):
        c = db.conn()
        self.assertGreaterEqual(c.execute('PRAGMA user_version').fetchone()[0], 24)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        self.assertTrue({'device_apps', 'device_app_reports', 'device_actions', 'device_actions_open', 'jobs_kind_id'} <= names)
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
        dh.add_job(kind='appmanage', target={'action_id': 1})
        with self.assertRaises(sqlite3.IntegrityError):   # sizes: all known or all null with a reason
            c.execute("INSERT INTO device_apps(device_id,package,version_code,app_bytes,reported_at) VALUES (?,'a.b',1,0,'t')",
                      (self.phone,))

    # inventory
    def test_parse_inventory_rules(self):
        self.assertHttp(400, inv, [app('a.b'), app('a.b')])
        self.assertHttp(400, inv, [app('a.b')], removed=['a.c'])            # full needs removed == []
        self.assertHttp(400, inv, [], full=False)                           # diff needs base_rev
        self.assertHttp(400, inv, [app('a.b', app_bytes=None)])             # mixed sizes
        self.assertHttp(400, inv, [app('a.b', app_bytes=None, data_bytes=None, cache_bytes=None)])   # no reason
        self.assertHttp(400, inv, [app('a.b', icon='AB' * 32)])
        self.assertHttp(400, inv, [app('a.b', label='')])
        got = inv([app('a.b', app_bytes=None, data_bytes=None, cache_bytes=None, size_reason='usage access not granted')])
        self.assertEqual(got.apps['a.b']['size_reason'], 'usage access not granted')

    def test_full_then_diff_then_stale(self):
        self.assertEqual(store.ingest(self.phone, inv([app('a.b'), app('a.c')]))['rev'], 1)
        r = store.ingest(self.phone, inv([app('a.b', 2), app('a.d')], full=False, base_rev=1, removed=['a.c']))
        self.assertEqual((r['rev'], r['accepted']), (2, True))
        self.assertEqual({p: e['version_code'] for p, e in store.inventory(self.phone)[self.phone].items()},
                         {'a.b': 2, 'a.d': 1})
        stale = store.ingest(self.phone, inv([app('a.e')], full=False, base_rev=1))
        self.assertEqual(stale, {'rev': 2, 'accepted': False, 'need_icons': []})
        self.assertNotIn('a.e', store.inventory(self.phone)[self.phone])
        self.assertEqual(store.ingest(self.tab, inv([], full=False, base_rev=1))['accepted'], False)   # no report yet
        self.assertEqual(store.ingest(self.phone, inv([app('a.z')]))['rev'], 3)                       # full replaces
        self.assertEqual(list(store.inventory()[self.phone]), ['a.z'])
        self.assertEqual(list(store.reports()), [self.phone])

    def test_ingest_full_adb(self):
        e = {k: v for k, v in app('a.b').items() if k != 'package'} | {'label': None}
        self.assertEqual(store.ingest_full(self.tab, {'a.b': e}), 1)
        self.assertEqual(store.ingest_full(self.tab, {}), 2)
        self.assertEqual(store.inventory(self.tab), {self.tab: {}})

    def test_need_icons_and_store_icon(self):
        other = 'cd' * 32
        r = store.ingest(self.phone, inv([app('a.b', icon=PNG_SHA), app('a.c', icon=other)]))
        self.assertEqual(r['need_icons'], sorted([PNG_SHA, other]))
        self.assertHttp(400, store.store_icon, other, PNG)                  # hash mismatch
        self.assertHttp(400, store.store_icon, PNG_SHA, b'GIF89a')
        big = b'\x89PNG\r\n\x1a\n' + b'x' * store.ICON_MAX_BYTES
        self.assertHttp(400, store.store_icon, hashlib.sha256(big).hexdigest(), big)
        store.store_icon(PNG_SHA, PNG)
        self.assertEqual(store.icon_path(PNG_SHA).read_bytes(), PNG)
        r = store.ingest(self.phone, inv([app('a.b', 2, icon=PNG_SHA)], full=False, base_rev=1))
        self.assertEqual(r['need_icons'], [other])

    # actions
    def test_create_replace_running_and_all_or_nothing(self):
        a1 = store.create_actions('a.b', 'install', [self.phone])[0]['id']
        self.assertEqual(store.actions(self.phone)[0]['source'], 'play_direct')    # untracked default
        a2 = store.create_actions('a.b', 'remove', [self.phone])[0]['id']
        self.assertEqual(tuple(self.state(a1)), ('cancelled', 'REPLACED', f'replaced by action {a2}'))
        store.pickup(self.phone)
        store.start(self.phone, a2)
        msg = self.assertHttp(409, store.create_actions, 'a.b', 'install', [self.phone])
        self.assertIn('device Phone is running remove of a.b', msg)
        n = len(store.actions())
        self.assertHttp(404, store.create_actions, 'a.c', 'install', [self.tab, 999])
        self.assertEqual(len(store.actions()), n)
        self.assertHttp(400, store.create_actions, 'a.c', 'remove', [self.tab], source='play_page')
        self.assertHttp(400, store.create_actions, 'a.c', 'install', [self.tab], source='build')
        self.assertHttp(400, store.create_actions, 'a.c', 'install', [])
        legacy = dh.add_app_device('Old')
        db.conn().execute('UPDATE devices SET device_uid=NULL WHERE id=?', (legacy,))
        self.assertHttp(409, store.create_actions, 'a.c', 'install', [legacy])

    def test_adb_action_enqueues_job(self):
        aid = store.create_actions('a.b', 'install', [self.tab], source='play_page')[0]['id']
        row = db.conn().execute("SELECT id, target FROM jobs WHERE kind='appmanage'").fetchone()
        self.assertEqual(row['target'], dh.canon({'action_id': aid}))
        claimed = store.claim_for_job(aid, row['id'])
        self.assertEqual((claimed['state'], claimed['job_id']), ('running', row['id']))
        self.assertIsNone(store.claim_for_job(aid, row['id']))

    def test_build_source_assigns_and_remove_unassigns(self):
        dh.add_catalog_item(4, 'app.x')
        self.assertEqual(store.tracked_refs()['app.x'], {'kind': 'item', 'id': 4, 'version_code': None,
                                                         'version_name': None})
        store.create_actions('app.x', 'install', [self.phone])
        self.assertEqual(store.actions(self.phone)[0]['ref_kind'], 'item')
        q = 'SELECT item_id FROM device_assignments WHERE device_id=?'
        self.assertEqual([r[0] for r in db.conn().execute(q, (self.phone,))], [4])
        store.create_actions('app.x', 'remove', [self.phone])
        self.assertEqual(db.conn().execute(q, (self.phone,)).fetchall(), [])

    def test_tracked_refs_item_wins_and_build_version(self):
        dh.add_catalog_item(4, 'app.x')
        apk = self.data / 'a.apk'
        apk.write_bytes(b'x')
        dh.add_build(4, 'app.x', str(apk), 'ee' * 32, version_code=9, version_name='9.0')
        dh.add_project(1, 'app.x')
        self.assertEqual(store.tracked_refs()['app.x'], {'kind': 'item', 'id': 4, 'version_code': 9, 'version_name': '9.0'})

    def test_pickup_start_finish(self):
        aid = store.create_actions('a.b', 'install', [self.phone], source='play_direct')[0]['id']
        self.assertHttp(409, store.start, self.phone, aid)                      # still queued
        self.assertEqual(store.pickup(self.phone), [{'id': aid, 'package': 'a.b', 'action': 'install',
                                                      'source': 'play_direct', 'ref': None}])
        self.assertEqual(store.pickup(self.phone), [])                          # delivered once
        self.assertHttp(404, store.start, self.tab, aid)                        # another device's id
        self.assertEqual(store.start(self.phone, aid)['state'], 'running')
        self.assertHttp(409, store.start, self.phone, aid)
        self.assertHttp(400, store.finish, self.phone, aid, 'failed', 'NOPE')
        self.assertEqual(store.finish(self.phone, aid, 'failed', 'PLAY_DOWNLOAD_FAILED', 'x')['state'], 'failed')
        self.assertHttp(409, store.finish, self.phone, aid, 'done')
        b = store.create_actions('a.c', 'remove', [self.phone])[0]['id']
        store.pickup(self.phone)
        done = store.finish(self.phone, b, 'cancelled')
        self.assertEqual((done['state'], done['error_code']), ('cancelled', 'CANCELLED_BY_USER'))

    def test_finish_by_receipt(self):
        dh.add_catalog_item(4, 'app.x')
        dh.add_catalog_item(5, 'app.y')
        x = store.create_actions('app.x', 'upgrade', [self.phone])[0]['id']
        y = store.create_actions('app.y', 'install', [self.phone])[0]['id']
        store.pickup(self.phone)
        store.finish_by_receipt(self.phone, 'app.x', 'installed', None, None)
        store.finish_by_receipt(self.phone, 'app.y', 'failed', 'USER_REJECTED', 'no')
        store.finish_by_receipt(self.phone, 'app.z', 'installed', None, None)    # no open action: no-op
        self.assertEqual(self.state(x)['state'], 'done')
        self.assertEqual(tuple(self.state(y))[:2], ('cancelled', 'CANCELLED_BY_USER'))
        z = store.create_actions('app.x', 'upgrade', [self.phone])[0]['id']
        store.finish_by_receipt(self.phone, 'app.x', 'failed', None, None)       # a receipt may omit the code
        self.assertEqual(tuple(self.state(z))[:2], ('failed', 'INSTALLER_ERROR'))

    def test_cancel_and_dismiss(self):
        a = store.create_actions('a.b', 'install', [self.phone])[0]['id']
        self.assertEqual(store.cancel(a), {'id': a, 'state': 'cancelled'})
        self.assertEqual(self.state(a)['error_code'], 'CANCELLED')
        self.assertHttp(409, store.cancel, a)
        b = store.create_actions('a.b', 'install', [self.phone])[0]['id']
        store.pickup(self.phone)
        store.finish(self.phone, b, 'failed', 'TIMEOUT')
        self.assertEqual(store.cancel(b), {'id': b, 'deleted': True})
        self.assertIsNone(self.state(b))
        self.assertHttp(404, store.cancel, b)

    # devices
    def test_primary(self):
        self.assertIsNone(store.get_primary())
        self.assertEqual(store.set_primary(self.phone), self.phone)
        self.assertEqual(store.get_primary(), self.phone)
        self.assertHttp(404, store.set_primary, 999)
        with db.tx() as c:
            c.execute('DELETE FROM devices WHERE id=?', (self.phone,))
        self.assertIsNone(store.get_primary())
        self.assertIsNone(store.set_primary(None))

    def test_move_device(self):
        store.ingest(self.phone, inv([app('a.phone')]))
        store.ingest_full(self.tab, {'a.tab': {k: v for k, v in app('a.tab').items() if k != 'package'}})
        mine = store.create_actions('a.b', 'install', [self.phone])[0]['id']
        theirs = store.create_actions('a.b', 'install', [self.tab], source='play_page')[0]['id']
        store.set_primary(self.phone)
        with db.tx() as c:
            store.move_device(c, self.phone, self.tab)
            c.execute('DELETE FROM devices WHERE id=?', (self.phone,))
        self.assertEqual(store.inventory(), {self.tab: {'a.phone': store.inventory()[self.tab]['a.phone']}})
        self.assertEqual(store.reports()[self.tab] is not None, True)
        self.assertEqual(tuple(self.state(theirs))[:2], ('cancelled', 'DEVICE_ADOPTED'))
        self.assertEqual({a['id'] for a in store.actions(self.tab)}, {mine, theirs})
        self.assertEqual(store.actions(self.phone), [])
        self.assertEqual(settings.get(store.PRIMARY_KEY), self.tab)

    def test_delete_cascade(self):
        store.ingest(self.phone, inv([app('a.b')]))
        store.create_actions('a.b', 'remove', [self.phone])
        with db.tx() as c:
            c.execute('DELETE FROM devices WHERE id=?', (self.phone,))
        for t in ('device_apps', 'device_app_reports', 'device_actions'):
            self.assertEqual(db.conn().execute(f'SELECT count(*) FROM {t}').fetchone()[0], 0, t)
