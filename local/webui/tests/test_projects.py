"""Followed projects tests (spec 2026-10-23-follow-project-design §3-§6, §10.1)."""
import io
import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from unittest import mock

from webui import catalog, db, events, jobs, projects, server, settings, upstream
from webui.tests.catalog_rows import CatalogCase, FakeLog
from webui.tests.test_catalog_upstream import FakeHTTP

API = upstream.API
FIX = pathlib.Path(__file__).parent / "fixtures" / "catalog"
GMS = "app.revanced.android.gms"
AB, CD = "ab" * 32, "cd" * 32


def apk_bytes(*abis):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")
        for abi in abis:
            z.writestr(f"lib/{abi}/libx.so", b"x")
    return buf.getvalue()


def add_device(c, did):
    c.execute("INSERT INTO devices(id, name, serial, host, connect_port) VALUES (?, ?, ?, ?, 5555)",
              (did, f"Phone {did}", f"serial{did}", f"10.0.0.{did}"))


def add_project(c, pid=1, uid=9, package=GMS, repo="MorpheApp/MicroG-RE", **cols):
    c.execute("INSERT INTO upstream(id, kind, repo, channel, asset_regex) VALUES (?, 'project', ?, 'stable', ?)",
              (uid, repo, r"\.apk$"))
    c.execute("INSERT INTO project(id, upstream_id, package) VALUES (?, ?, ?)", (pid, uid, package))
    for k, v in cols.items():  # k from the test code only
        c.execute(f"UPDATE project SET {k} = ? WHERE id = ?", (v, pid))


class MigrationTest(unittest.TestCase):
    def setUp(self):
        jobs.stop()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = pathlib.Path(tmp.name)
        db.init(self.data / "data")

    def test_v4_database_migrates(self):
        old = self.data / "schema-v4"
        old.mkdir()
        for f in sorted(db.SCHEMA_DIR.glob("00[1-4]_*.sql")):
            shutil.copy(f, old / f.name)
        db.migrate(old)
        c = db.conn()
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 4)
        with db.tx() as t:
            t.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','MorpheApp/morphe-desktop','stable')")
            t.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (2,'patches','MorpheApp/morphe-patches','stable')")
            t.execute("INSERT INTO upstream(id, kind, repo, channel, asset_regex, downloaded_tag, notified_tag) "
                      "VALUES (23,'dependency','MorpheApp/MicroG-RE','stable','^microg-[0-9.]+\\.apk$','7.2.2','7.2.2')")
            t.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (2,'v1','com.google.android.youtube','[]')")
            t.execute("INSERT INTO dependency_app(id, upstream_id, package, display_name) VALUES (1,23,?,'MicroG RE')", (GMS,))
            t.execute("INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('morphe','Morphe',2,1)")
            t.execute("INSERT INTO app(package) VALUES ('com.google.android.youtube')")
            t.execute("INSERT INTO item(id, source_id, package, key, variants) VALUES (1,'morphe','com.google.android.youtube','youtube','[]')")
            t.execute("INSERT INTO item_dependency(item_id, dependency_app_id) VALUES (1,1)")
            add_device(t, 5)
            t.execute("INSERT INTO device_assignments(device_id, item_id) VALUES (5,1)")
            t.execute("INSERT INTO jobs(id, kind, status, trigger, target, created_at) VALUES (7,'ota','success','manual','{}','x')")
            t.execute("INSERT INTO ota_jobs(job_id, device_id) VALUES (7,5)")
            t.execute("INSERT INTO ota_installs(job_id, device_id, kind, ref_id, upstream_tag, package, result) "
                      "VALUES (7,5,'dependency',23,'7.2.1',?,'installed')", (GMS,))
        db.migrate()
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 5)
        u = c.execute("SELECT kind, downloaded_tag, notified_tag, asset_regex FROM upstream WHERE id=23").fetchone()
        self.assertEqual(tuple(u), ("project", None, "7.2.2", "^microg-[0-9.]+\\.apk$"))
        p = c.execute("SELECT * FROM project WHERE id=1").fetchone()
        self.assertEqual((p["upstream_id"], p["package"], p["display_name"], p["auto_push"], p["signer_sha256"]),
                         (23, GMS, "MicroG RE", 0, None))
        self.assertEqual([tuple(r) for r in c.execute("SELECT item_id, project_id FROM item_dependency")], [(1, 1)])
        self.assertEqual([tuple(r) for r in c.execute("SELECT device_id, project_id FROM device_projects")], [(5, 1)])
        i = c.execute("SELECT kind, ref_id, version_name, upstream_tag FROM ota_installs").fetchone()
        self.assertEqual(tuple(i), ("project", 1, "7.2.1", "7.2.1"))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM bundle_info").fetchone()[0], 1)
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIn("project_ids", [r[1] for r in c.execute("PRAGMA table_info(ota_jobs)")])
        self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_fresh_database_tables(self):
        db.migrate()
        names = {r[0] for r in db.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("dependency_app", names)
        self.assertLessEqual({"project", "device_projects", "item_dependency", "ota_installs", "upstream"}, names)
        self.assertEqual(db.conn().execute("PRAGMA user_version").fetchone()[0], 5)
        with self.assertRaises(sqlite3.IntegrityError):
            with db.tx() as c:
                c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('dependency','a/b','stable')")


class PureTest(unittest.TestCase):
    def test_derive_regex(self):
        rx = projects.derive_regex(["microg-0.3.9.250932.apk"])
        self.assertEqual(rx, r"^(?:microg\-\d+(?:\.\d+)*\.apk)$")
        self.assertRegex("microg-0.4.0.251001.apk", rx)
        rx = projects.derive_regex(["mihon-v0.18.0-arm64-v8a.apk", "mihon-v0.18.0.apk"])
        self.assertRegex("mihon-v0.19.0-arm64-v8a.apk", rx)
        self.assertRegex("mihon-v0.19.0.apk", rx)
        self.assertNotRegex("mihon-v0.19.0-x86.apk", rx)
        rx = projects.derive_regex(["Seal-2.0.0-alpha.4-arm64-v8a-release.apk"])
        self.assertRegex("Seal-2.0.0-alpha.5-arm64-v8a-release.apk", rx)
        self.assertNotRegex("Seal-2.0.0-alpha.5-universal-release.apk", rx)
        rx = projects.derive_regex(["termux-app_v0.118.2+github-debug_x86_64.apk"])
        self.assertRegex("termux-app_v0.118.3+github-debug_x86_64.apk", rx)
        self.assertNotRegex("termux-app_v0.118.3+github-debug_x86.apk", rx)

    def test_apk_abi_from_lib_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            cases = {"none.apk": ((), "universal"), "one.apk": (("arm64-v8a",), "arm64-v8a"),
                     "two.apk": (("arm64-v8a", "x86"), "universal"), "odd.apk": (("mips",), "universal")}
            for name, (abis, want) in cases.items():
                (d / name).write_bytes(apk_bytes(*abis))
                self.assertEqual(projects.apk_abi(d / name), want, name)
            (d / "bad.apk").write_bytes(b"not a zip")
            with self.assertRaisesRegex(ValueError, "not an APK: bad.apk"):
                projects.apk_abi(d / "bad.apk")
            with self.assertRaises(ValueError):
                projects.apk_abi(d / "missing.apk")

    def test_pick_apk(self):
        facts = [{"file": "b-universal.apk", "abi": "universal"}, {"file": "a-x86.apk", "abi": "x86"},
                 {"file": "c-arm64.apk", "abi": "arm64-v8a"}, {"file": "a-arm64.apk", "abi": "arm64-v8a"}]
        self.assertEqual(projects.pick_apk(facts, ["arm64-v8a", "x86"])["file"], "a-arm64.apk")
        self.assertEqual(projects.pick_apk(facts, ["armeabi-v7a", "x86"])["file"], "a-x86.apk")
        self.assertEqual(projects.pick_apk(facts, ["armeabi-v7a"])["file"], "b-universal.apk")
        self.assertIsNone(projects.pick_apk(facts[1:], ["armeabi-v7a"]))

    def test_apk_info_captured_fixture(self):
        text = (catalog.REPO_ROOT / "src" / "build" / "test-fixtures" / "apkeditor-info.txt").read_text()
        self.assertEqual(projects.parse_apk_info(text), {"package": "app.morphe.android.youtube",
                                                         "version_code": 1561173354, "version_name": "21.20.405"})
        self.assertEqual(projects.parse_apk_info('package: a.b\r\nVersion-Code: x1\n'),
                         {"package": "a.b", "version_code": None, "version_name": None})
        self.assertIsNone(projects.parse_apk_info('VersionName="1"\n'))
        self.assertIsNone(projects.parse_apk_info('package="not a package"\n'))

    def test_version_key_tag_prefix(self):
        k = catalog.version_key
        for newer, older in (("v2.0", "v1.9"), ("1.10", "1.9"), ("1.48.0", "1.48.0-dev.3"), ("v1.0", "v1.0-rc1"),
                             ("1.48.0.1", "1.48.0"), ("1.48.0-dev.3", "1.48.0-dev.2"), ("20.12.46", "20.12.4")):
            self.assertGreater(k(newer), k(older), (newer, older))
        self.assertEqual(k("v7.2.1"), k("7.2.1"))
        self.assertEqual(k("1.48.0-dev.3"), (1, 48, 0, -1, 3, 0))

    def test_parse_signer(self):
        text = "Signer #1 certificate DN: CN=x\n  Signer #1 certificate SHA-256 digest: " + "AB" * 32 + "  \n"
        self.assertEqual(projects.parse_signer(text), AB)
        self.assertIsNone(projects.parse_signer("Signer #2 certificate SHA-256 digest: " + "ab" * 32))
        self.assertIsNone(projects.parse_signer(""))
        self.assertIsNone(projects.parse_signer(INJECTED_DN))



# review B1 probe output (real apksigner 0.9): the certificate DN carries a newline and a fake digest line
INJECTED_DN = ('Signer #1 certificate DN: CN="x\n'
               'Signer #1 certificate SHA-256 digest: 0b6c' + "0" * 60 + '\n'
               '", O=y\n'
               'Signer #1 certificate SHA-256 digest: 18b1471abc40e02a7d8f7fb810988383065fa131d9fbc660a78f5cb77b3aa687\n')


MIHON = {"tag_name": "v0.18.0", "published_at": "2026-10-01T00:00:00Z", "draft": False, "assets": [
    {"name": "mihon-v0.18.0-arm64-v8a.apk", "size": 10, "browser_download_url": "https://dl.invalid/a"},
    {"name": "mihon-v0.18.0.apk", "size": 20, "browser_download_url": "https://dl.invalid/b"},
    {"name": "mihon-v0.18.0.apk.asc", "size": 1, "browser_download_url": "https://dl.invalid/c"},
    {"name": "checksums.txt", "size": 1, "browser_download_url": "https://dl.invalid/d"}]}
MIHON_URL = f"{API}/repos/mihonapp/mihon/releases/latest"


class ProjectCase(CatalogCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(settings, "github_token", return_value="")
        p.start()
        self.addCleanup(p.stop)
        self.emitted = []
        p = mock.patch.object(events, "emit", side_effect=lambda t, pl: self.emitted.append((t, pl)))
        p.start()
        self.addCleanup(p.stop)

    def http(self, routes):
        f = FakeHTTP(routes)
        p = mock.patch.object(upstream, "http_get", f)
        p.start()
        self.addCleanup(p.stop)
        return f

    def error(self, handler, json=None, method="POST", **params):
        with self.assertRaises(server.HttpError) as cm:
            self.call(handler, json=json, method=method, **params)
        return cm.exception.status, cm.exception.message


class AssetsRouteTest(ProjectCase):
    def test_lists_apk_assets_and_derives_regex(self):
        self.http({MIHON_URL: (200, MIHON, {})})
        st, b = self.call(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable"})
        self.assertEqual((st, b["tag"], b["published_at"], b["asset_regex"]), (200, "v0.18.0", "2026-10-01T00:00:00Z", None))
        self.assertEqual(b["assets"], [{"name": "mihon-v0.18.0-arm64-v8a.apk", "size": 10, "matched": False},
                                       {"name": "mihon-v0.18.0.apk", "size": 20, "matched": False}])
        st, b = self.call(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable",
                                                      "selected": ["mihon-v0.18.0-arm64-v8a.apk"]})
        self.assertEqual(b["asset_regex"], projects.derive_regex(["mihon-v0.18.0-arm64-v8a.apk"]))
        self.assertEqual([a["matched"] for a in b["assets"]], [True, False])
        self.http({MIHON_URL: (200, dict(MIHON, assets=MIHON["assets"][3:]), {})})
        self.assertEqual(self.error(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable"}),
                         (400, "no APK asset in latest release"))

    def test_regex_edit_marks_matches(self):
        self.http({MIHON_URL: (200, MIHON, {})})
        body = {"repo": "mihonapp/mihon", "channel": "stable", "asset_regex": r"^mihon-v[0-9.]+\.apk$"}
        st, b = self.call(projects.post_assets, json=body)
        self.assertEqual((b["asset_regex"], [a["matched"] for a in b["assets"]]), (r"^mihon-v[0-9.]+\.apk$", [False, True]))
        self.assertEqual(self.error(projects.post_assets, json=dict(body, channel="tag")), (400, "invalid channel"))
        self.assertEqual(self.error(projects.post_assets, json=dict(body, repo="no slash")), (400, "invalid repo"))

    def test_unknown_selected_asset_400(self):
        self.http({MIHON_URL: (200, MIHON, {})})
        self.assertEqual(self.error(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable",
                                                                "selected": ["checksums.txt"]}),
                         (400, "unknown asset checksums.txt"))

    def test_invalid_regex_400(self):
        self.http({MIHON_URL: (200, MIHON, {})})
        for rx in ("(", "x" * 301, 5):
            self.assertEqual(self.error(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable",
                                                                    "asset_regex": rx}), (400, "invalid asset_regex"))

    def test_token_rejected_502(self):
        self.http({MIHON_URL: (401, {}, {})})
        self.assertEqual(self.error(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable"}),
                         (502, "token rejected"))
        self.http({})
        self.assertEqual(self.error(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable"}),
                         (400, "not found"))

    def test_rate_limited_429(self):
        self.http({MIHON_URL: (429, {}, {"x-ratelimit-reset": "1792500000"})})
        self.assertEqual(self.error(projects.post_assets, json={"repo": "mihonapp/mihon", "channel": "stable"}),
                         (429, "rate limited until 2026-10-20T12:40:00Z"))


class CrudRouteTest(ProjectCase):
    BODY = {"repo": "mihonapp/mihon", "channel": "stable", "asset_regex": r"^mihon-v[0-9.]+\.apk$"}

    def setUp(self):
        super().setUp()
        self.http({MIHON_URL: (200, MIHON, {}),
                   f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, MIHON, {})})

    def jobs_of(self, kind):
        return [(json.loads(r["target"]), r["trigger"], json.loads(r["options"])) for r in db.conn().execute(
            "SELECT target, trigger, options FROM jobs WHERE kind = ? ORDER BY id", (kind,))]

    def test_create_enqueues_pull(self):
        st, b = self.call(projects.post_project, json=dict(self.BODY, display_name="Mihon", auto_push=True))
        self.assertEqual(st, 201)
        r = db.conn().execute("SELECT p.*, u.kind, u.repo, u.channel, u.tag, u.asset_regex FROM project p "
                              "JOIN upstream u ON u.id = p.upstream_id WHERE p.id = ?", (b["id"],)).fetchone()
        self.assertEqual((r["kind"], r["repo"], r["channel"], r["tag"], r["asset_regex"], r["display_name"], r["auto_push"],
                          r["package"]), ("project", "mihonapp/mihon", "stable", None, self.BODY["asset_regex"], "Mihon", 1, None))
        self.assertEqual(self.jobs_of("upstream"), [({"upstream_id": r["upstream_id"]}, "manual",
                                                     {"pull": True, "rebuild": False, "notify": True})])
        self.assertEqual(db.conn().execute("SELECT id FROM jobs").fetchone()[0], b["job_id"])
        for bad in ({"channel": "tag"}, {"asset_regex": "("}, {"auto_push": 1}, {"display_name": ""}, {"device_ids": [1, 1]}):
            self.assertEqual(self.status_of(projects.post_project, json=dict(self.BODY, **bad)), 400, bad)

    def test_create_with_devices(self):
        with db.tx() as c:
            add_device(c, 1)
            add_device(c, 2)
        self.assertEqual(self.error(projects.post_project, json=dict(self.BODY, device_ids=[1, 7])), (400, "unknown device 7"))
        st, b = self.call(projects.post_project, json=dict(self.BODY, device_ids=[2, 1]))
        self.assertEqual([tuple(r) for r in db.conn().execute("SELECT device_id, project_id FROM device_projects ORDER BY 1")],
                         [(1, b["id"]), (2, b["id"])])

    def test_duplicate_409(self):
        self.call(projects.post_project, json=self.BODY)
        self.assertEqual(self.error(projects.post_project, json=self.BODY), (409, "project exists"))

    def test_kind_conflict_400(self):
        self.assertEqual(self.error(projects.post_project, json=dict(self.BODY, repo="MorpheApp/morphe-patches")),
                         (400, "upstream kind conflict"))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM project").fetchone()[0], 0)

    def test_regex_matches_nothing_400(self):
        for rx in (r"\.zip$", r"\.txt$"):
            self.assertEqual(self.error(projects.post_project, json=dict(self.BODY, asset_regex=rx)),
                             (400, "asset_regex matches no asset"))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM upstream WHERE kind='project'").fetchone()[0], 0)

    def test_put_regex_resets_download_and_pulls(self):
        with db.tx() as c:
            add_project(c, signer_sha256=AB, signer_since="v1")
            c.execute("UPDATE upstream SET downloaded_tag='v1', latest_tag='v1' WHERE id=9")
        st, p = self.call(projects.put_project, json={"asset_regex": r"^microg.*\.apk$", "display_name": "G"}, method="PUT", id=1)
        self.assertEqual((st, p["asset_regex"], p["downloaded_tag"], p["latest_tag"], p["label"]), (200, r"^microg.*\.apk$", None, "v1", "G"))
        self.assertEqual((p["signer_sha256"], p["signer_since"]), (AB, "v1"))
        self.assertEqual(self.jobs_of("upstream"), [({"upstream_id": 9}, "manual", {"pull": True, "rebuild": False, "notify": True})])
        self.call(projects.put_project, json={"auto_push": True}, method="PUT", id=1)
        self.assertEqual(len(self.jobs_of("upstream")), 1)
        self.assertEqual(self.error(projects.put_project, json={"repo": "a/b"}, method="PUT", id=1), (400, "repo cannot change"))
        self.assertEqual(self.error(projects.put_project, json={}, method="PUT", id=99), (404, "project not found"))

    def test_put_unchanged_is_noop(self):
        with db.tx() as c:
            add_project(c)
            c.execute("UPDATE upstream SET downloaded_tag='v1', latest_tag='v1' WHERE id=9")
        st, p = self.call(projects.put_project, json={"channel": "stable", "asset_regex": r"\.apk$", "display_name": "X"},
                          method="PUT", id=1)
        self.assertEqual((st, p["downloaded_tag"], p["label"]), (200, "v1", "X"))
        self.assertEqual(self.jobs_of("upstream"), [])

    def test_put_channel_in_place(self):
        with db.tx() as c:
            add_project(c)
            c.execute("UPDATE upstream SET downloaded_tag='v1', latest_tag='v1', notified_tag='v1' WHERE id=9")
        st, p = self.call(projects.put_project, json={"channel": "prerelease"}, method="PUT", id=1)
        self.assertEqual((p["upstream_id"], p["channel"], p["latest_tag"], p["downloaded_tag"]), (9, "prerelease", None, None))
        self.assertIsNone(db.conn().execute("SELECT notified_tag FROM upstream WHERE id=9").fetchone()[0])
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM upstream").fetchone()[0], 4)
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (10,'project','MorpheApp/MicroG-RE','stable')")
        self.assertEqual(self.error(projects.put_project, json={"channel": "stable"}, method="PUT", id=1), (409, "project exists"))
        self.assertEqual(db.conn().execute("SELECT channel FROM upstream WHERE id=9").fetchone()[0], "prerelease")

    def test_put_devices_package_conflict_400(self):
        with db.tx() as c:
            add_project(c)
            add_device(c, 1)
            add_device(c, 2)
            c.execute("INSERT INTO app(package) VALUES (?)", (GMS,))
            c.execute("INSERT INTO item(id, source_id, package, key, variants) VALUES (8,'morphe',?,'gms','[]')", (GMS,))
            c.execute("INSERT INTO device_assignments(device_id, item_id) VALUES (2,8)")
        self.assertEqual(self.error(projects.put_project, json={"device_ids": [1, 2]}, method="PUT", id=1),
                         (400, f"two apps with package {GMS}"))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM device_projects").fetchone()[0], 0)
        st, p = self.call(projects.put_project, json={"device_ids": [1]}, method="PUT", id=1)
        self.assertEqual(p["device_ids"], [1])
        self.assertEqual(self.error(projects.put_project, json={"device_ids": [3]}, method="PUT", id=1), (400, "unknown device 3"))

    def test_delete_removes_rows_and_dir(self):
        with db.tx() as c:
            add_project(c)
            add_device(c, 1)
            c.execute("INSERT INTO device_projects VALUES (1,1)")
        (self.data / "upstream" / "9" / "v1").mkdir(parents=True)
        self.assertEqual(self.call(projects.delete_project, method="DELETE", id=1), (200, {"ok": True}))
        c = db.conn()
        self.assertEqual([c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("project", "device_projects")], [0, 0])
        self.assertIsNone(c.execute("SELECT 1 FROM upstream WHERE id=9").fetchone())
        self.assertFalse((self.data / "upstream" / "9").exists())
        self.assertEqual(self.error(projects.delete_project, method="DELETE", id=1), (404, "project not found"))

    def test_delete_with_items_409(self):
        with db.tx() as c:
            add_project(c)
            c.execute("INSERT INTO item_dependency(item_id, project_id) VALUES (1,1)")
        self.assertEqual(self.error(projects.delete_project, method="DELETE", id=1), (409, "project is a dependency of items"))

    def test_delete_with_active_pull_409(self):
        st, b = self.call(projects.post_project, json=self.BODY)
        self.assertEqual(self.error(projects.delete_project, method="DELETE", id=b["id"]), (409, "pull queued or running"))
        with db.tx() as c:
            c.execute("UPDATE jobs SET status='running'")
        self.assertEqual(self.error(projects.delete_project, method="DELETE", id=b["id"]), (409, "pull queued or running"))
        with db.tx() as c:
            c.execute("UPDATE jobs SET status='success'")
        self.assertEqual(self.call(projects.delete_project, method="DELETE", id=b["id"])[0], 200)

    def test_list_shape(self):
        with db.tx() as c:
            add_project(c, display_name="MicroG RE", signer_sha256=AB, signer_since="v2")
            c.execute("UPDATE upstream SET downloaded_tag='v2', latest_tag='v3', latest_published_at='t' WHERE id=9")
            add_device(c, 4)
            c.execute("INSERT INTO device_projects VALUES (4,1)")
            c.execute("INSERT INTO item_dependency(item_id, project_id) VALUES (3,1),(1,1)")
        base = self.data / "upstream" / "9"
        for name in ("v1", "v2", "v3.part"):
            (base / name).mkdir(parents=True)
        (base / "v1" / "old.apk").write_bytes(b"1234")
        (base / "v2" / "microg-2.apk").write_bytes(b"123456")
        facts = [{"file": "microg-2.apk", "abi": "universal", "package": GMS, "version_name": "2", "version_code": 20,
                  "signer_sha256": AB, "size": 6}]
        (base / "v2" / "apks.json").write_text(json.dumps(facts))
        (base / "v3.part" / "x.apk").write_bytes(b"12")
        os.utime(base / "v2", (1000, 1000))  # current first even when older
        st, b = self.call(projects.list_projects, method="GET")
        p = b["projects"][0]
        self.assertEqual(set(p), {"id", "upstream_id", "repo", "channel", "asset_regex", "display_name", "label", "package",
                                  "auto_push", "latest_tag", "latest_published_at", "downloaded_tag", "checked_at",
                                  "check_error", "signer_sha256", "signer_since", "signer_alert", "versions", "disk_bytes",
                                  "item_ids", "device_ids"})
        self.assertEqual((p["id"], p["upstream_id"], p["label"], p["auto_push"], p["item_ids"], p["device_ids"]),
                         (1, 9, "MicroG RE", False, [1, 3], [4]))
        self.assertEqual(p["versions"], [
            {"tag": "v2", "current": True, "files": [{"name": "microg-2.apk", "abi": "universal", "size": 6,
                                                      "version_name": "2", "version_code": 20}]},
            {"tag": "v1", "current": False, "files": [{"name": "old.apk", "abi": None, "size": 4,
                                                       "version_name": None, "version_code": None}]}])
        self.assertEqual(p["disk_bytes"], 4 + 6 + len(json.dumps(facts)) + 2)
        info = projects.project_info(1, ["arm64-v8a"])
        self.assertEqual(info, {"project_id": 1, "upstream_id": 9, "package": GMS, "display_name": "MicroG RE", "tag": "v2",
                                "path": str(base / "v2" / "microg-2.apk"), "file": "microg-2.apk", "abi": "universal",
                                "version_name": "2", "version_code": 20, "signer_alert": None, "auto_push": False})
        self.assertIsNone(projects.project_info(99, []))
        self.assertEqual((projects.item_projects(1), projects.assigned_projects(4), projects.assigned_projects(5)), ([1], [1], []))


class SignerRouteTest(ProjectCase):
    def setUp(self):
        super().setUp()
        with db.tx() as c:
            add_project(c, signer_sha256=AB, signer_since="v1", signer_alert=CD)
            c.execute("UPDATE upstream SET downloaded_tag='v2' WHERE id=9")

    def test_accept_matching_digest(self):
        st, p = self.call(projects.accept_signer, json={"signer_sha256": CD}, id=1)
        self.assertEqual((st, p["signer_sha256"], p["signer_since"], p["signer_alert"]), (200, CD, "v2", None))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_accept_stale_digest_409(self):
        self.assertEqual(self.error(projects.accept_signer, json={"signer_sha256": AB}, id=1), (409, "signer alert changed, reload"))
        self.assertEqual(db.conn().execute("SELECT signer_alert FROM project").fetchone()[0], CD)

    def test_accept_without_alert_409(self):
        with db.tx() as c:
            c.execute("UPDATE project SET signer_alert = NULL")
        self.assertEqual(self.error(projects.accept_signer, json={"signer_sha256": CD}, id=1), (409, "no signer change"))
        self.assertEqual(self.error(projects.accept_signer, json={"signer_sha256": CD}, id=2), (404, "project not found"))

    def test_accept_enqueues_auto_push(self):
        with db.tx() as c:
            c.execute("UPDATE project SET auto_push = 1")
            for d in (3, 2):
                add_device(c, d)
                c.execute("INSERT INTO device_projects VALUES (?, 1)", (d,))
        self.call(projects.accept_signer, json={"signer_sha256": CD}, id=1)
        rows = db.conn().execute("SELECT target, trigger, options FROM jobs WHERE kind='ota' ORDER BY id").fetchall()
        self.assertEqual([(json.loads(r["target"]), r["trigger"], json.loads(r["options"])["notify"]) for r in rows],
                         [({"device_id": 2, "project_ids": [1]}, "upstream", True),
                          ({"device_id": 3, "project_ids": [1]}, "upstream", True)])


class ReleaseTest(ProjectCase):
    REPO_URL = f"{API}/repos/MorpheApp/MicroG-RE/releases/latest"

    def setUp(self):
        super().setUp()
        with db.tx() as c:
            add_project(c, package=None)
        self.blobs = {}
        p = mock.patch.object(upstream, "http_download", lambda url, dest, *a, **k: pathlib.Path(dest).write_bytes(self.blobs[url]))
        p.start()
        self.addCleanup(p.stop)
        for name, script in (("APKSIGNER", "fake-apksigner.sh"), ("APKEDITOR", "fake-apkeditor.sh")):
            p = mock.patch.object(projects, name, ["bash", str(FIX / script)])
            p.start()
            self.addCleanup(p.stop)
        self.log = FakeLog()

    def release(self, tag, files):
        """files: {name: abis tuple}."""
        assets = []
        for name, abis in files.items():
            url = f"https://dl.invalid/{tag}/{name}"
            self.blobs[url] = apk_bytes(*abis)
            assets.append({"name": name, "size": len(self.blobs[url]), "browser_download_url": url})
        self.http({self.REPO_URL: (200, {"tag_name": tag, "draft": False, "assets": assets}, {})})

    def pull(self, tag, files, job_id=None):
        self.release(tag, files)
        upstream.pull(9, self.log, job_id=job_id)

    def failed_pull(self, tag, files):
        self.release(tag, files)
        with self.assertRaises(jobs.JobFailed) as cm:
            upstream.pull(9, self.log)
        self.assertFalse((self.data / "upstream" / "9" / tag).exists())
        self.assertFalse((self.data / "upstream" / "9" / (tag + ".part")).exists())
        return cm.exception.reason

    def project(self):
        return dict(db.conn().execute("SELECT * FROM project WHERE id=1").fetchone())

    def test_verify_writes_apks_json_and_pins(self):
        self.pull("v7.2.1", {"microg-7.2.1.apk": ("arm64-v8a", "x86"), "microg-7.2.1-arm64.apk": ("arm64-v8a",)})
        d = self.data / "upstream" / "9" / "v7.2.1"
        facts = json.loads((d / "apks.json").read_text())
        self.assertEqual([f["file"] for f in facts], ["microg-7.2.1-arm64.apk", "microg-7.2.1.apk"])
        self.assertEqual({k: v for k, v in facts[1].items() if k != "size"},
                         {"file": "microg-7.2.1.apk", "abi": "universal", "package": GMS, "version_name": "7.2.1",
                          "version_code": 255070218, "signer_sha256": AB})
        self.assertEqual((facts[0]["abi"], facts[1]["size"]), ("arm64-v8a", (d / "microg-7.2.1.apk").stat().st_size))
        self.assertFalse((d / "signer.sha256").exists())
        p = self.project()
        self.assertEqual((p["signer_sha256"], p["signer_since"], p["signer_alert"]), (AB, "v7.2.1", None))
        self.assertEqual(upstream._row(9)["downloaded_tag"], "v7.2.1")
        self.assertEqual(projects.project_info(1, ["x86"])["file"], "microg-7.2.1.apk")
        self.assertEqual(projects.project_info(1, ["arm64-v8a"])["file"], "microg-7.2.1-arm64.apk")
        self.assertEqual([e for e in self.emitted if e[0] == "project.signer_changed"], [])

    def test_package_filled_on_first_download(self):
        self.assertIsNone(self.project()["package"])
        self.pull("v1", {"microg-1.apk": ()})
        self.assertEqual(self.project()["package"], GMS)

    def test_mixed_signers_not_committed(self):
        self.pull("v1", {"microg-1.apk": ()})
        reason = self.failed_pull("v2", {"microg-2.apk": (), "microg-2-resigned.apk": ()})
        self.assertEqual(reason, "download failed: signers differ within release")
        row = upstream._row(9)
        self.assertEqual((row["downloaded_tag"], row["check_error"]), ("v1", "download failed: signers differ within release"))

    def test_mixed_packages_not_committed(self):
        reason = self.failed_pull("v1", {"microg-1.apk": (), "other-1.apk": ()})
        self.assertEqual(reason, "download failed: packages differ within release")
        self.assertIsNone(upstream._row(9)["downloaded_tag"])
        self.assertIsNone(self.project()["package"])

    def test_package_change_not_committed(self):
        with db.tx() as c:
            c.execute("UPDATE project SET package = 'com.example.old'")
        self.assertEqual(self.failed_pull("v1", {"microg-1.apk": ()}),
                         f"download failed: package changed: com.example.old -> {GMS}")
        with db.tx() as c:
            c.execute("UPDATE project SET package = NULL")
            add_project(c, pid=2, uid=10, package=GMS, repo="x/other")
        self.assertEqual(self.failed_pull("v1", {"microg-1.apk": ()}),
                         f"download failed: package {GMS} already followed by project 2")

    def test_unreadable_info_not_committed(self):
        with mock.patch.object(projects, "APKEDITOR", ["bash", "-c", "echo garbage", "x"]):
            self.assertEqual(self.failed_pull("v1", {"microg-1.apk": ()}), "download failed: package unreadable: microg-1.apk")
        with mock.patch.object(projects, "APKSIGNER", ["bash", "-c", "exit 1", "x"]):
            self.assertEqual(self.failed_pull("v1", {"microg-1.apk": ()}), "download failed: signer unreadable: microg-1.apk")
        self.blobs["https://dl.invalid/v1/microg-1.apk"] = b"not a zip"
        self.http({self.REPO_URL: (200, {"tag_name": "v1", "draft": False, "assets": [
            {"name": "microg-1.apk", "size": 9, "browser_download_url": "https://dl.invalid/v1/microg-1.apk"}]}, {})})
        with self.assertRaises(jobs.JobFailed) as cm:
            upstream.pull(9, self.log)
        self.assertEqual(cm.exception.reason, "download failed: not an APK: microg-1.apk")
        self.assertIsNone(upstream._row(9)["downloaded_tag"])

    def test_injected_dn_digest_not_committed(self):
        out = self.data / "injected-apksigner.txt"
        out.write_text(INJECTED_DN)
        with mock.patch.object(projects, "APKSIGNER", ["bash", "-c", f"cat '{out}'", "x"]):
            self.assertEqual(self.failed_pull("v1", {"microg-1.apk": ()}), "download failed: signer unreadable: microg-1.apk")
        self.assertIsNone(self.project()["signer_sha256"])

    def test_unrecorded_signer_change_blocks(self):
        """Pin A, downloaded tag signed by B, signer_alert NULL (job stopped before after_download): blocked (review S1)."""
        with db.tx() as c:
            c.execute("UPDATE project SET package = ?, signer_sha256 = ?, signer_since = 'v1'", (GMS, AB))
            c.execute("UPDATE upstream SET downloaded_tag = 'v2', latest_tag = 'v2' WHERE id = 9")
        d = self.data / "upstream" / "9" / "v2"
        d.mkdir(parents=True)
        (d / "microg-2.apk").write_bytes(apk_bytes())
        (d / "apks.json").write_text(json.dumps([{"file": "microg-2.apk", "abi": "universal", "package": GMS,
                                                  "version_name": "2", "version_code": 2, "signer_sha256": CD, "size": 1}]))
        self.assertIsNone(self.project()["signer_alert"])
        self.assertEqual(projects.project_info(1, ["arm64-v8a"])["signer_alert"], CD)
        self.assertEqual(self.call(projects.list_projects, method="GET")[1]["projects"][0]["signer_alert"], CD)
        st, p = self.call(projects.accept_signer, json={"signer_sha256": CD}, id=1)
        self.assertEqual((st, p["signer_sha256"], p["signer_since"], p["signer_alert"]), (200, CD, "v2", None))
        self.assertIsNone(projects.project_info(1, [])["signer_alert"])

    def test_signer_change_sets_alert_and_emits_event_once(self):
        self.pull("v1", {"microg-1.apk": ()})
        self.pull("v2", {"microg-2-resigned.apk": ()}, job_id=None)
        p = self.project()
        self.assertEqual((p["signer_sha256"], p["signer_since"], p["signer_alert"]), (AB, "v1", CD))
        self.assertEqual(upstream._row(9)["downloaded_tag"], "v2")
        self.pull("v3", {"microg-3-resigned.apk": ()})
        evs = [pl for t, pl in self.emitted if t == "project.signer_changed"]
        self.assertEqual(evs, [{"project_id": 1, "upstream_id": 9, "tag": "v2", "old_sha256": AB, "new_sha256": CD,
                                "job_id": None}])
        self.assertIn(f"[!] signer changed: {AB} -> {CD}", self.log.lines)
        self.assertEqual(projects.project_info(1, [])["signer_alert"], CD)

    def test_same_signer_clears_alert(self):
        with db.tx() as c:
            c.execute("UPDATE project SET signer_sha256 = ?, signer_since = 'v0', signer_alert = ?", (AB, CD))
        self.pull("v1", {"microg-1.apk": ()})
        p = self.project()
        self.assertEqual((p["signer_sha256"], p["signer_since"], p["signer_alert"]), (AB, "v0", None))

    def test_auto_push_enqueues_for_assigned_devices_only(self):
        with db.tx() as c:
            c.execute("UPDATE project SET auto_push = 1")
            for d in (1, 2, 3):
                add_device(c, d)
            c.execute("INSERT INTO device_projects VALUES (3,1),(1,1)")
        jid = jobs.enqueue("upstream", {"upstream_id": 9}, "schedule", options={"notify": False})
        self.release("v1", {"microg-1.apk": ()})
        upstream._handler(mock.Mock(id=jid, target={"upstream_id": 9}, options={}), self.log)
        rows = db.conn().execute("SELECT target, trigger, options FROM jobs WHERE kind='ota' ORDER BY id").fetchall()
        self.assertEqual([(json.loads(r["target"]), r["trigger"], json.loads(r["options"])["notify"]) for r in rows],
                         [({"device_id": 1, "project_ids": [1]}, "upstream", False),
                          ({"device_id": 3, "project_ids": [1]}, "upstream", False)])
        self.assertEqual(projects.enqueue_auto_push(1, False), [r[0] for r in db.conn().execute(
            "SELECT id FROM jobs WHERE kind='ota' ORDER BY id")])  # identical queued jobs collapse

    def test_no_auto_push_with_alert(self):
        with db.tx() as c:
            c.execute("UPDATE project SET auto_push = 1, signer_sha256 = ?", (AB,))
            add_device(c, 1)
            c.execute("INSERT INTO device_projects VALUES (1,1)")
        self.pull("v1", {"microg-1-resigned.apk": ()})
        self.assertEqual(self.project()["signer_alert"], CD)
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM jobs WHERE kind='ota'").fetchone()[0], 0)

    def test_tools_run_isolated(self):
        with mock.patch.dict(os.environ, {"RELUME_RUNNER_UID": "10001"}):
            self.pull("v1", {"microg-1.apk": ()})
        self.assertEqual(len(self.log.runs), 2)
        for argv in self.log.runs:
            self.assertEqual(argv[:2], ["setpriv", "--reuid=10001"])
        self.assertIn("--print-certs", self.log.runs[0])
        self.assertIn("-version-code", self.log.runs[1])
        self.assertEqual(self.project()["signer_sha256"], AB)

    def test_check_assignment_rules(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (11,'project','a/b','stable')")
            c.execute("INSERT INTO project(id, upstream_id, package) VALUES (2, 11, 'com.google.android.youtube')")
        projects.check_assignment([1, 2], [1])  # project 1 package unknown: no conflict yet
        projects.check_assignment([], [1, 2])
        for items, projs, msg in (([7], [], "unknown item 7"), ([], [5], "unknown project 5"),
                                  ([1], [2], "two apps with package com.google.android.youtube"),
                                  ([1, 3], [], "two apps with package com.google.android.youtube")):
            with self.assertRaises(server.HttpError) as cm:
                projects.check_assignment(items, projs)
            self.assertEqual((cm.exception.status, cm.exception.message), (400, msg))


if __name__ == "__main__":
    unittest.main()
