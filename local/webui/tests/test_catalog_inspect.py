"""Inspector, patch selection, version pin and job input tests (spec catalog §4, §5.2-5.3, §10)."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from webui import catalog, db, upstream
from webui.tests.catalog_rows import CatalogCase, FakeLog
from webui.tests.test_catalog_upstream import API, UpstreamCase, fix

FIX = Path(__file__).parent / "fixtures" / "catalog"
JOB_FIXTURE = json.loads((catalog.REPO_ROOT / "src" / "build" / "test-fixtures" / "job.json").read_text())
YT = "com.google.android.youtube"
TEXT = """Index: 0
Name: Hide ads
Description: Removes ads.
Enabled: true
Options:
\tTitle: Mode
\tDescription: How much
\tRequired: false
\tKey: mode
\tDefault: full
\tPossible values:
\t\tfull
\t\tlite
\tType: String
\tTitle: Count
\tKey: count
\tDefault: 3
\tType: Int
Compatible packages:
\tPackage name: com.google.android.youtube
\tCompatible versions:
\t\t20.12.46
\t\t20.13.41
Index: 1
Name: Spoof
Description: Spoofs.
Enabled: false
"""


class ParserTest(unittest.TestCase):
    def test_handwritten_layout(self):
        ps = upstream.parse_list_patches(TEXT)
        self.assertEqual(ps[0], {"name": "Hide ads", "description": "Removes ads.", "enabled": True,
                                 "options": [{"key": "mode", "title": "Mode", "type": "String", "default": "full", "values": ["full", "lite"]},
                                             {"key": "count", "title": "Count", "type": "Int", "default": "3", "values": []}],
                                 "packages": {YT: ["20.12.46", "20.13.41"]}})
        self.assertEqual((ps[1]["name"], ps[1]["enabled"], ps[1]["options"], ps[1]["packages"]), ("Spoof", False, [], {}))

    def test_captured_fixture(self):
        text = (FIX / "list-patches.txt").read_text()
        ps = upstream.parse_list_patches(text)
        self.assertEqual(len(ps), len(re.findall(r"(?m)^(?:INFO: )?\s*Index:", text)))
        self.assertTrue(all(p["name"] for p in ps))
        self.assertTrue(any(p["options"] for p in ps))
        self.assertTrue(all(o["key"] for p in ps for o in p["options"]))
        self.assertTrue([v for p in ps for v in p["packages"].get(YT, [])])
        self.assertEqual(ps[0]["name"], "Clone app")  # first block carries the logger prefix

    def test_merge_bundle(self):
        opts = json.dumps([{"patches": {"Hide ads": {"enabled": False, "options": {"mode": "lite"}}}}])
        m = upstream.merge_bundle(TEXT, opts, YT)
        self.assertEqual(m[0]["default_enabled"], False)
        self.assertEqual([o["default"] for o in m[0]["options"]], ["lite", "3"])
        self.assertEqual(m[0]["compatible_versions"], ["20.13.41", "20.12.46"])
        self.assertEqual(m[1]["default_enabled"], False)
        m2 = upstream.merge_bundle((FIX / "list-patches.txt").read_text(), (FIX / "options.json").read_text(), YT)
        self.assertTrue(m2 and all(isinstance(p["default_enabled"], bool) for p in m2))


class PatchesCase(CatalogCase):
    def setUp(self):
        super().setUp()
        self.ptag = self.data / "upstream" / "2" / "v1"
        self.ptag.mkdir(parents=True)
        (self.ptag / "patches-1.mpp").write_bytes(b"x")
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag='v1' WHERE id=2")
            c.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (2,'v1',?,?)",
                      (YT, json.dumps(upstream.merge_bundle(TEXT, "[]", YT))))
            c.execute("UPDATE item SET patch_overrides=? WHERE id=1",
                      (json.dumps({"Hide ads": {"enabled": False}, "Gone": {"enabled": True}}),))

    def add_cli(self):
        d = self.data / "upstream" / "1" / "v1.18.1"
        d.mkdir(parents=True)
        (d / "morphe-desktop-1.18.1-all.jar").write_bytes(b"x")
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag='v1.18.1' WHERE id=1")


class PatchesRouteTest(PatchesCase):
    def test_get_patches(self):
        st, b = self.call(catalog.get_patches, method="GET", id=1)
        self.assertEqual((b["tag"], b["files"], b["unavailable"]), ("v1", ["patches-1.mpp"], False))
        self.assertEqual([(p["name"], p["enabled"]) for p in b["patches"]], [("Hide ads", False), ("Spoof", False)])
        self.assertEqual(b["stale_overrides"], ["Gone"])
        self.assertEqual(b["compatible_versions"], ["20.13.41", "20.12.46"])
        self.assertEqual(b["patches"][0]["options"][0]["value"], "full")

    def test_get_patches_unavailable(self):
        st, b = self.call(catalog.get_patches, method="GET", id=2)
        self.assertEqual((b["unavailable"], b["patches"], b["stale_overrides"]), (True, [], []))

    def test_put_patches_validation(self):
        ok = {"Hide ads": {"enabled": True, "options": {"mode": "lite", "count": 4}}}
        self.assertEqual(self.call(catalog.put_patches, json={"overrides": ok}, method="PUT", id=1)[0], 200)
        stored = json.loads(db.conn().execute("SELECT patch_overrides FROM item WHERE id=1").fetchone()[0])
        self.assertEqual(stored, {"Gone": {"enabled": True}, "Hide ads": {"enabled": True, "options": {"mode": "lite", "count": 4}}})
        self.call(catalog.put_patches, json={"overrides": {}}, method="PUT", id=1)
        stored = json.loads(db.conn().execute("SELECT patch_overrides FROM item WHERE id=1").fetchone()[0])
        self.assertEqual(stored, {"Gone": {"enabled": True}})
        bads = [{"Nope": {"enabled": True}}, {"Gone": {"enabled": False}}, {"Hide ads": {"options": {"nokey": 1}}},
                {"Hide ads": {"options": {"count": "4"}}}, {"Hide ads": {"enabled": "yes"}}, {"Hide ads": {"extra": 1}}]
        for bad in bads:
            self.assertEqual(self.status_of(catalog.put_patches, json={"overrides": bad}, method="PUT", id=1), 400, bad)
        self.assertEqual(self.status_of(catalog.put_patches, json={"overrides": {}}, method="PUT", id=2), 409)

    def test_cli_type_names(self):
        self.assertTrue(catalog._type_ok("kotlin.Boolean", True))
        self.assertFalse(catalog._type_ok("kotlin.Boolean", "true"))
        self.assertTrue(catalog._type_ok("kotlin.collections.List<kotlin.String>", ["a"]))
        self.assertFalse(catalog._type_ok("kotlin.String", 3))

    def test_put_version(self):
        self.assertEqual(self.status_of(catalog.put_version, json={"version_mode": "pinned"}, method="PUT", id=1), 400)
        st, it = self.call(catalog.put_version, json={"version_mode": "pinned", "version_pin": "20.12.46"}, method="PUT", id=1)
        self.assertEqual(it["version"], {"mode": "pinned", "pin": "20.12.46", "allow_nearest": False})
        st, it = self.call(catalog.put_version, json={"version_mode": "latest", "version_pin": "x", "allow_nearest": True}, method="PUT", id=1)
        self.assertEqual(it["version"], {"mode": "latest", "pin": None, "allow_nearest": True})
        self.assertEqual(self.status_of(catalog.put_version, json={"version_mode": "auto", "allow_nearest": "x"}, method="PUT", id=1), 400)
        self.assertEqual(self.status_of(catalog.put_version, json={"version_mode": "never"}, method="PUT", id=1), 400)


class JobInputTest(PatchesCase):
    def test_upstream_missing(self):
        with self.assertRaises(catalog.UpstreamMissing):
            catalog.job_input(1)  # CLI not downloaded
        self.add_cli()
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag=NULL WHERE id=2")
        with self.assertRaises(catalog.UpstreamMissing):
            catalog.job_input(1)

    def test_shape_matches_runner_fixture(self):
        self.add_cli()
        j = catalog.job_input(1)
        self.assertEqual(set(j), set(JOB_FIXTURE) - {"job_id", "work_dir"})
        self.assertEqual(set(j["recipe"]["variants"][0]), set(JOB_FIXTURE["recipe"]["variants"][0]))
        self.assertEqual(set(j["recipe"]["variants"][0]["download"]), set(JOB_FIXTURE["recipe"]["variants"][0]["download"]))
        self.assertEqual(set(j["version"]), set(JOB_FIXTURE["version"]))
        self.assertEqual(set(j["apkmirror"]), set(JOB_FIXTURE["apkmirror"]))
        self.assertEqual(j["options_file_json"], [{"patches": {"Hide ads": {"enabled": False}}}])
        self.assertEqual(j["patches_files"], [str(self.ptag / "patches-1.mpp")])
        self.assertTrue(j["cli_jar"].endswith("morphe-desktop-1.18.1-all.jar"))
        self.assertEqual(catalog.stale_overrides(1), ["Gone"])

    def test_empty_overrides(self):
        self.add_cli()
        with db.tx() as c:
            c.execute("UPDATE item SET patch_overrides='{}' WHERE id=1")
        self.assertEqual(catalog.job_input(1)["options_file_json"], [{"patches": {}}])

    def test_versions_due(self):
        self.add_cli()
        self.assertTrue(catalog.job_input(1)["apkmirror"]["versions_due"])
        for hours, due in ((25, True), (1, False)):
            t = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
            with db.tx() as c:
                c.execute("UPDATE app SET apkmirror_checked_at=? WHERE package=?", (t, YT))
            self.assertEqual(catalog.job_input(1)["apkmirror"]["versions_due"], due)

    def test_set_apkmirror_versions(self):
        catalog.set_apkmirror_versions(YT, ["20.14.1", "20.13.41"])
        it = catalog.item_detail(1)
        self.assertEqual(it["apkmirror"]["versions"], ["20.14.1", "20.13.41"])
        self.assertIsNotNone(it["apkmirror"]["checked_at"])


class InspectRowTest(PatchesCase):
    def test_inspect_writes_bundle_info(self):
        self.add_cli()
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info")
        with mock.patch.object(upstream, "INSPECT", str(FIX / "fake-inspect.sh")):
            upstream.inspect_row(2, "v1", FakeLog())
        pk = {r[0] for r in db.conn().execute("SELECT package FROM bundle_info WHERE upstream_id=2 AND tag='v1'")}
        self.assertEqual(pk, {YT, "com.reddit.frontpage"})

    def test_inspect_failure_writes_nothing(self):
        self.add_cli()
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info")
        log = FakeLog()
        with mock.patch.object(upstream, "INSPECT", str(FIX / "fake-inspect-fail.sh")):
            upstream.inspect_row(2, "v1", log)
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM bundle_info").fetchone()[0], 0)
        self.assertTrue(any(l.startswith("[!] inspect failed for") for l in log.lines))


class ReinspectTest(UpstreamCase):
    def test_pull_reinspects_missing_bundle_with_same_tag(self):
        d = self.data / "upstream" / "2" / "v1.47.0"
        d.mkdir(parents=True)
        (d / "patches-1.47.0.mpp").write_bytes(b"hello")
        c1 = self.data / "upstream" / "1" / "v1.18.1"
        c1.mkdir(parents=True)
        (c1 / "morphe-desktop-1.18.1-all.jar").write_bytes(b"x")
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag='v1.47.0' WHERE id=2")
            c.execute("UPDATE upstream SET downloaded_tag='v1.18.1' WHERE id=1")
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, fix("release-stable.json"), {})})
        with mock.patch.object(upstream, "INSPECT", str(FIX / "fake-inspect.sh")):
            upstream.pull(2, FakeLog())
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM bundle_info WHERE tag='v1.47.0'").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
