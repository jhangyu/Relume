from webui import db, devices
from webui.tests import helpers
from webui.tests.device_helpers import add_catalog_item, add_device, req


class AssignDisabledTest(helpers.DataTestCase):
    def rows(self, d):
        return [r[0] for r in db.conn().execute('SELECT item_id FROM device_assignments WHERE device_id=? ORDER BY item_id', (d,))]

    def put(self, d, ids):
        devices.put_assignments(req(params={'id': d}, body={'item_ids': ids}))

    def set_enabled(self, item_id, enabled):
        with db.tx() as c:
            c.execute('UPDATE item SET enabled=? WHERE id=?', (enabled, item_id))

    def test_full_list_put_keeps_disabled_rows(self):
        add_catalog_item(1, 'a.a')
        add_catalog_item(2, 'b.b')
        d = add_device()
        self.put(d, [1, 2])
        self.set_enabled(2, 0)
        self.put(d, [1])
        self.assertEqual(self.rows(d), [1, 2])
        self.set_enabled(2, 1)
        self.assertEqual(self.rows(d), [1, 2])
        self.set_enabled(2, 0)
        self.put(d, [])
        self.assertEqual(self.rows(d), [2])
        self.put(d, [2])  # a disabled id sent explicitly is still accepted
        self.assertEqual(self.rows(d), [2])
