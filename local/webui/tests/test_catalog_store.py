"""Catalog store tests (spec catalog §3, §6, §10)."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui import catalog, db, server, upstream
from webui.tests import helpers
from webui.tests.catalog_rows import CatalogCase

PLAY = (Path(__file__).parent / "fixtures" / "catalog" / "play-youtube.html").read_bytes()


class CatalogSchemaTest(helpers.DataTestCase):
    def test_tables(self):
        names = {r[0] for r in db.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"upstream", "source", "app", "grp", "item", "bundle_info",
                              "project", "item_dependency", "device_projects"}, names)

    def test_upstream_unique_with_null_tag(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('patches','a/b','stable')")
        with self.assertRaises(sqlite3.IntegrityError):
            with db.tx() as c:
                c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('patches','a/b','stable')")

    def test_group_delete_and_item_cascade(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','c/c','stable'),(2,'patches','p/p','stable'),(3,'project','m/m','stable')")
            c.execute("INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('s','S',2,1)")
            c.execute("INSERT INTO app(package) VALUES ('com.a.b')")
            c.execute("INSERT INTO grp(id, name, position) VALUES (1,'G',0)")
            c.execute("INSERT INTO item(id, source_id, package, key, group_id, position, variants) VALUES (1,'s','com.a.b','b',1,0,'[]')")
            c.execute("INSERT INTO project(id, upstream_id, package, display_name) VALUES (1,3,'x.y','X')")
            c.execute("INSERT INTO item_dependency(item_id, project_id) VALUES (1,1)")
            c.execute("DELETE FROM grp WHERE id=1")
        self.assertIsNone(db.conn().execute("SELECT group_id FROM item WHERE id=1").fetchone()[0])
        with db.tx() as c:
            c.execute("DELETE FROM item WHERE id=1")
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM item_dependency").fetchone()[0], 0)


class MigrationAutoUpdateTest(helpers.DataTestCase):
    """006: item.auto_update defaults to 0 for existing rows and the jobs rebuild keeps rows and accepts kind appcheck."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for f in sorted(db.SCHEMA_DIR.glob("00[1-5]_*.sql")):
            shutil.copy(f, tmp.name)
        self.schema_dir = tmp.name
        super().setUp()
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','c/c','stable'),(2,'patches','p/p','stable')")
            c.execute("INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('s','S',2,1)")
            c.execute("INSERT INTO app(package) VALUES ('com.a.b')")
            c.execute("INSERT INTO item(id, source_id, package, key, position, variants) VALUES (1,'s','com.a.b','b',0,'[]')")
            c.execute("INSERT INTO jobs(id, kind, status, trigger, target, created_at) VALUES (7,'build','success','manual','{}','t')")
            c.execute("INSERT INTO build_jobs(job_id, item_id) VALUES (7, 1)")
        db.migrate()

    def test_column_default_kind_and_child_rows(self):
        self.assertEqual(db.conn().execute("SELECT auto_update FROM item WHERE id = 1").fetchone()[0], 0)
        self.assertEqual(db.conn().execute("SELECT kind, status FROM jobs WHERE id = 7").fetchone()[:], ("build", "success"))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM build_jobs").fetchone()[0], 1)
        with db.tx() as c:
            c.execute("INSERT INTO jobs(kind, status, trigger, target, created_at) VALUES ('appcheck','queued','manual','{}','t')")
        with self.assertRaises(sqlite3.IntegrityError):
            with db.tx() as c:
                c.execute("INSERT INTO jobs(kind, status, trigger, target, created_at) VALUES ('nope','queued','manual','{}','t')")


class GroupsTest(CatalogCase):
    def test_colour_create_update_clear(self):
        st, g = self.call(catalog.post_group, json={"name": "Fav", "color": "#1a2B3c"})
        self.assertEqual(st, 201)
        view = self.call(catalog.get_catalog, method="GET")[1]
        self.assertEqual([x["color"] for x in view["groups"] if x["id"] == g["id"]], ["#1a2B3c"])
        for bad in ("red", "#123", 5):
            self.assertEqual(self.status_of(catalog.put_group, json={"color": bad}, method="PUT", id=g["id"]), 400)
        self.call(catalog.put_group, json={"color": None}, method="PUT", id=g["id"])
        self.assertIsNone(catalog._group(g["id"])["color"])
        self.assertEqual(catalog._group(g["id"])["position"], 2)

    def test_group_order(self):
        self.call(catalog.put_groups_order, json={"ids": [2, 1]}, method="PUT")
        self.assertEqual([g["id"] for g in catalog._groups()], [2, 1])
        self.assertEqual(self.status_of(catalog.put_groups_order, json={"ids": [1]}, method="PUT"), 400)

    def test_item_reorder_and_move(self):
        self.call(catalog.put_group_items_order, json={"item_ids": [2, 1]}, method="PUT", id=1)
        view = catalog.catalog_view()
        self.assertEqual([(i["id"], i["position"]) for i in view["groups"][0]["items"]], [(2, 0), (1, 1)])
        self.call(catalog.put_group_items_order, json={"item_ids": [3]}, method="PUT", id=1)
        view = catalog.catalog_view()
        self.assertEqual([(i["id"], i["position"]) for i in view["groups"][0]["items"]], [(3, 0), (2, 1), (1, 2)])
        self.assertEqual(view["groups"][1]["items"], [])
        self.call(catalog.put_group_items_order, json={"item_ids": [1]}, method="PUT", id=0)
        self.assertEqual([i["id"] for i in catalog.catalog_view()["ungrouped"]], [1])
        self.assertEqual(self.status_of(catalog.put_group_items_order, json={"item_ids": [99]}, method="PUT", id=1), 400)
        self.assertEqual(self.status_of(catalog.put_group_items_order, json={"item_ids": [1]}, method="PUT", id=42), 404)

    def test_delete_group_moves_items_to_ungrouped(self):
        self.call(catalog.delete_group, method="DELETE", id=1)
        view = catalog.catalog_view()
        self.assertEqual([i["id"] for i in view["ungrouped"]], [1, 2])
        self.assertEqual([(g["id"], g["position"]) for g in view["groups"]], [(2, 0)])

    def test_item_object_fields(self):
        it = catalog.catalog_view()["groups"][0]["items"][0]
        self.assertEqual(it["label"], "YouTube · Morphe")
        self.assertEqual(it["abis"], ["universal", "arm64-v8a"])
        self.assertIsNone(it["icon_url"])
        self.assertEqual(it["upstream"]["repo"], "MorpheApp/morphe-patches")
        self.assertIn("active_job", it)
        reddit = catalog.catalog_view()["groups"][0]["items"][1]
        self.assertEqual(reddit["label"], "com.reddit.frontpage · Morphe")


class ResolveTest(CatalogCase):
    def test_enabled_group_items(self):
        with db.tx() as c:
            c.execute("UPDATE item SET enabled=0 WHERE id=2")
        self.assertEqual(catalog.enabled_group_items(1), [1])
        self.assertEqual(catalog.enabled_group_items(2), [3])
        with db.tx() as c:
            c.execute("UPDATE item SET group_id=NULL WHERE id=1")
        self.assertEqual(catalog.enabled_group_items(0), [1])


class CrudTest(CatalogCase):
    SRC = {"id": "hoo", "name": "Hoo", "engine": "morphe", "repo": "hoo-dles/morphe-patches",
           "channel": "stable", "keystore": "morphe.keystore", "cli_upstream_id": 1}

    def test_source_create_dedups_upstream(self):
        self.assertEqual(self.call(catalog.post_source, json=self.SRC)[0], 201)
        b = dict(self.SRC, id="hoo2")
        self.call(catalog.post_source, json=b)
        n = db.conn().execute("SELECT COUNT(*) FROM upstream WHERE repo='hoo-dles/morphe-patches'").fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(self.status_of(catalog.post_source, json=self.SRC), 409)

    def test_source_validation(self):
        for bad in ({"repo": "nope"}, {"keystore": "missing.ks"}, {"cli_upstream_id": 2}, {"cli_upstream_id": True},
                    {"channel": "tag"}, {"asset_regex": "("}, {"engine": "other"}):
            self.assertEqual(self.status_of(catalog.post_source, json=dict(self.SRC, **bad)), 400, bad)

    def test_source_delete_with_items(self):
        self.assertEqual(self.status_of(catalog.delete_source, method="DELETE", id="morphe"), 409)
        self.call(catalog.post_source, json=self.SRC)
        self.assertEqual(self.call(catalog.delete_source, method="DELETE", id="hoo")[0], 200)

    def test_item_create_defaults_and_duplicate(self):
        st, body = self.call(catalog.post_item, json={"source_id": "morphe", "package": "com.duolingo", "group_id": 1})
        self.assertEqual(st, 201)
        it = catalog.item_detail(body["id"])
        self.assertEqual([v["abi"] for v in it["variants"]], ["universal", "arm64-v8a"])
        self.assertEqual(it["variants"][1]["strip_libs"], "arm64-v8a")
        self.assertEqual(it["key"], "duolingo")
        self.assertEqual(it["position"], 2)
        self.assertEqual((it["enabled"], it["auto_update"]), (False, False))
        self.assertIsNotNone(db.conn().execute("SELECT 1 FROM app WHERE package='com.duolingo'").fetchone())
        self.assertEqual(self.status_of(catalog.post_item, json={"source_id": "morphe", "package": "com.duolingo"}), 409)
        self.assertEqual(self.status_of(catalog.post_item, json={"source_id": "morphe", "package": "bad"}), 400)

    def test_item_put_auto_update_validation(self):
        st, it = self.call(catalog.put_item, json={"auto_update": True}, method="PUT", id=1)
        self.assertEqual((st, it["auto_update"], it["enabled"]), (200, True, True))
        self.assertEqual(db.conn().execute("SELECT auto_update FROM item WHERE id = 1").fetchone()[0], 1)
        for bad in (1, "true", None):
            self.assertEqual(self.status_of(catalog.put_item, json={"auto_update": bad}, method="PUT", id=1), 400, bad)

    def test_item_create_enqueues_pull_when_bundle_missing(self):
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag='v1' WHERE id=2")
        self.call(catalog.post_item, json={"source_id": "morphe", "package": "com.duolingo"})
        rows = db.conn().execute("SELECT target, options FROM jobs WHERE kind='upstream'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["target"]), {"upstream_id": 2})
        self.assertEqual(json.loads(rows[0]["options"])["rebuild"], False)
        with db.tx() as c:
            c.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (2,'v1','com.tumblr','[]')")
            c.execute("DELETE FROM jobs")
        self.call(catalog.post_item, json={"source_id": "morphe", "package": "com.tumblr"})
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_item_delete(self):
        self.build.active[1] = {"job_id": 9, "status": "running"}
        self.assertEqual(self.status_of(catalog.delete_item, method="DELETE", id=1), 409)
        del self.build.active[1]
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (9,'project','MorpheApp/MicroG-RE','stable')")
            c.execute("INSERT INTO project(id, upstream_id, package, display_name) VALUES (1,9,'app.revanced.android.gms','MicroG RE')")
            c.execute("INSERT INTO item_dependency VALUES (1,1)")
        self.assertEqual(self.call(catalog.delete_item, method="DELETE", id=1)[0], 200)
        self.assertEqual(self.build.forgotten, [1])
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM item_dependency").fetchone()[0], 0)

    def test_variant_validation(self):
        ok = catalog.validate_variants([dict(v) for v in catalog.default_variants()])
        self.assertEqual(len(ok), 2)
        base = catalog.default_variants()[0]
        bads = [[], [dict(base, name="../x")], [dict(base, abi="mips")], [base, base],
                [dict(base, download=dict(base["download"], kind="ftp"))],
                [dict(base, download=dict(base["download"], kind="url"))],
                [dict(base, download=dict(base["download"], dpi="-x"))],
                [dict(base, exclude_splits=["ok", "../bad"])], [dict(base, strip_libs="mips")]]
        for bad in bads:
            with self.assertRaises(server.HttpError, msg=str(bad)):
                catalog.validate_variants(bad)
        catalog.validate_variants([dict(base, download=dict(base["download"], arch="arm64-v8a + armeabi-v7a"))])

    def test_item_project_ids(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (9,'project','MorpheApp/MicroG-RE','stable')")
            c.execute("INSERT INTO project(id, upstream_id, package, display_name) VALUES (1,9,'app.revanced.android.gms','MicroG RE')")
        self.assertEqual(self.call(catalog.get_item, method="GET", id=1)[1]["project_ids"], [])
        st, it = self.call(catalog.put_item, json={"project_ids": [1]}, method="PUT", id=1)
        self.assertEqual((st, it["project_ids"]), (200, [1]))
        self.assertEqual(self.call(catalog.get_item, method="GET", id=1)[1]["project_ids"], [1])
        with self.assertRaises(server.HttpError) as cm:
            self.call(catalog.put_item, json={"project_ids": [5]}, method="PUT", id=1)
        self.assertEqual((cm.exception.status, cm.exception.message), (400, "unknown project"))
        self.assertEqual(self.status_of(catalog.put_item, json={"project_ids": "1"}, method="PUT", id=1), 400)
        self.call(catalog.put_item, json={"project_ids": []}, method="PUT", id=1)
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM item_dependency").fetchone()[0], 0)

    def test_put_app(self):
        st, a = self.call(catalog.put_app, json={"display_name": "Reddit", "apkpure_url": None}, method="PUT", pkg="com.reddit.frontpage")
        self.assertEqual(a["display_name"], "Reddit")
        self.assertEqual(self.status_of(catalog.put_app, json={"apkmirror_list_url": "http://x"}, method="PUT", pkg="com.reddit.frontpage"), 400)


class IconTest(CatalogCase):
    def fake_http(self, play_status=200):
        def get(url, headers=None, timeout=30):
            if url.startswith("https://play.google.com/"):
                return play_status, {}, PLAY if play_status == 200 else b""
            if url.startswith("https://play-lh.googleusercontent.com/"):
                return 200, {"content-type": "image/png"}, b"\x89PNG\r\n"
            return 404, {}, b""
        return mock.patch.object(upstream, "http_get", get)

    def test_fetch_sets_name_and_icon(self):
        with db.tx() as c:
            c.execute("UPDATE app SET display_name='Mine' WHERE package='com.google.android.youtube'")
        with self.fake_http():
            self.assertTrue(catalog.fetch_icon("com.reddit.frontpage"))
            self.assertTrue(catalog.fetch_icon("com.google.android.youtube"))
        r = db.conn().execute("SELECT display_name, icon_file, icon_fetched_at FROM app WHERE package='com.reddit.frontpage'").fetchone()
        self.assertEqual((r["display_name"], r["icon_file"]), ("YouTube", "com.reddit.frontpage.png"))
        self.assertTrue((self.data / "icons" / "com.reddit.frontpage.png").is_file())
        self.assertEqual(catalog.item_detail(2)["icon_url"], "/api/apps/com.reddit.frontpage/icon")
        self.assertEqual(db.conn().execute("SELECT display_name FROM app WHERE package='com.google.android.youtube'").fetchone()[0], "Mine")
        self.assertIsInstance(self.call(catalog.get_icon, method="GET", pkg="com.reddit.frontpage"), server.FileResponse)

    def test_fetch_failure_is_letter_tile(self):
        with self.fake_http(play_status=404):
            self.assertFalse(catalog.fetch_icon("com.reddit.frontpage"))
        r = db.conn().execute("SELECT icon_file, icon_fetched_at FROM app WHERE package='com.reddit.frontpage'").fetchone()
        self.assertIsNone(r["icon_file"])
        self.assertIsNotNone(r["icon_fetched_at"])
        self.assertEqual(self.status_of(catalog.get_icon, method="GET", pkg="com.reddit.frontpage"), 404)

    def test_refresh_clears_fetched_at(self):
        with self.fake_http(play_status=404):
            catalog.fetch_icon("com.reddit.frontpage")
        catalog._icon_wake.clear()
        self.assertEqual(self.call(catalog.post_icon_refresh, json={}, pkg="com.reddit.frontpage")[1], {"ok": True})
        self.assertIsNone(db.conn().execute("SELECT icon_fetched_at FROM app WHERE package='com.reddit.frontpage'").fetchone()[0])
        self.assertTrue(catalog._icon_wake.is_set())


if __name__ == "__main__":
    unittest.main()
