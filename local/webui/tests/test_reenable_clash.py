from webui import apps, db, devices
from webui.tests.device_helpers import add_device, add_project, assign_projects, req
from webui.tests.test_apps import YT, AppsCase


class ReenableClashTest(AppsCase):
    def test_reenable_drops_assignment_clashing_with_assigned_project(self):
        self.bundles({"bundle_id": self.builtin, "patches": [{"name": "Hide ads"}]})
        d = add_device()
        with db.tx() as c:
            c.execute("INSERT INTO device_assignments(device_id,item_id) VALUES (?,1)", (d,))
        self.set_item(1, enabled=0)
        add_project(1, YT)
        assign_projects(d, 1)
        apps.enable(YT, self.builtin)
        rows = [r[0] for r in db.conn().execute("SELECT item_id FROM device_assignments WHERE device_id=?", (d,))]
        self.assertEqual(rows, [])  # project wins, the phone's list is clash-free
        status, _ = devices.put_assignments(req(params={"id": d}, body={"item_ids": [], "project_ids": [1]}))
        self.assertEqual(status, 200)
