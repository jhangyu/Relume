"""An app removed on the server (item.enabled = 0, assignment rows kept) must not break a phone's plan request
or a later project assignment."""
from webui import db, ota, projects
from webui.server import HttpError
from webui.tests import helpers
from webui.tests.device_helpers import add_app_device, add_catalog_item, add_project, assign, req

PKG = 'com.example.removed'


class ServerRemovedAppTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        add_catalog_item(1, PKG, enabled=0)      # removed in Apps: row kept, assignment kept
        self.d = add_app_device()
        assign(self.d, 1)

    def test_phone_plan_with_cached_removed_item_is_empty_not_400(self):
        r = req(query={'items': '1'})
        r.device_id = self.d
        self.assertEqual(ota.me_plan(r)[1]['plan']['steps'], [])

    def test_project_with_removed_apps_package_can_be_assigned(self):
        add_project(2, PKG)
        projects._set_devices(db.conn(), 2, [self.d])   # raised HttpError 'two apps with package'
