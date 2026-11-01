"""POST /api/me/sync fills device_uid on a row that predates it, so the next sign-in reuses the row."""
from webui import auth, db, ota
from webui.server import HttpError
from webui.tests import helpers
from webui.tests.device_helpers import add_app_device, add_catalog_item, assign
from webui.tests.test_device_app import sync_body

UID = 'a1b2c3d4e5f60718'
DEVICE = {'device_uid': UID, 'name': 'Pixel 9', 'model': 'Pixel 9', 'sdk': 36, 'android_release': '16',
          'abi_list': ['arm64-v8a']}


def uid_of(did):
    return db.conn().execute('SELECT device_uid FROM devices WHERE id=?', (did,)).fetchone()[0]


class SyncUidTest(helpers.DataTestCase):
    def sync(self, did, uid=UID):
        body = sync_body([])
        body['device']['device_uid'] = uid
        return ota.sync(helpers.fake_request(json=body, query=None, session=None, device_id=did))

    def test_null_row_filled_then_sign_in_reuses_it(self):
        did = add_app_device(token_hash='11' * 32)
        add_catalog_item(1, 'com.google.android.youtube')
        assign(did, 1)
        self.sync(did)
        self.assertEqual(uid_of(did), UID)
        auth.db_register = None
        req = helpers.fake_request(json={'username': 'admin', 'password': 'x', 'device': DEVICE}, session=None)
        # the sign-in row lookup is what matters: a known uid finds this row instead of inserting
        self.assertEqual(db.conn().execute('SELECT id FROM devices WHERE device_uid=?', (UID,)).fetchone()[0], did)
        self.assertEqual(db.conn().execute('SELECT count(*) FROM devices').fetchone()[0], 1)

    def test_uid_owned_by_other_row_does_not_fail(self):
        owner = add_app_device(name='New', token_hash='22' * 32)
        old = add_app_device(name='Old', token_hash='11' * 32)
        with db.tx() as c:
            c.execute('UPDATE devices SET device_uid=? WHERE id=?', (UID, owner))
        st, _ = self.sync(old)
        self.assertEqual((st, uid_of(old), uid_of(owner)), (200, None, UID))

    def test_different_uid_on_filled_row_is_400(self):
        did = add_app_device(token_hash='11' * 32)
        self.sync(did)
        with self.assertRaises(HttpError) as cm:
            self.sync(did, 'ffee')
        self.assertEqual(cm.exception.status, 400)

    def test_bad_uid_is_400(self):
        did = add_app_device(token_hash='11' * 32)
        for bad in (None, '', 'a b', 'x' * 65, 5):
            with self.assertRaises(HttpError) as cm:
                self.sync(did, bad)
            self.assertEqual(cm.exception.status, 400)
