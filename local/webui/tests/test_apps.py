"""Enabled-app model, build input, rebuild fingerprint and update run (spec §3, apps part §4-§5, §8)."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from webui import apklocate, apps, build, catalog, db, jobs, server, upstream
from webui.tests.catalog_rows import CatalogCase, FakeLog, add_bundle, add_item

YT, REDDIT = "com.google.android.youtube", "com.reddit.frontpage"
JOB_FIXTURE = json.loads((db.REPO_ROOT / "src" / "build" / "test-fixtures" / "job.json").read_text())
CHECK_FIX = Path(__file__).parent / "fixtures" / "build"
OPTION = {"key": "mode", "title": "Mode", "description": "", "type": "kotlin.String", "default": "full",
          "required": False, "values": None}


def patch(name, versions=("20.12.46", "20.13.41"), options=(), default=True):
    return {"name": name, "description": f"{name}.", "default_enabled": default, "options": list(options),
            "compatible_versions": list(versions)}


class AppsCase(CatalogCase):
    def setUp(self):
        super().setUp()
        self.builtin = self.basic["builtin"]
        self.up = db.conn().execute("SELECT upstream_id FROM bundle WHERE id = ?", (self.builtin,)).fetchone()[0]
        self.download(self.up, "v1", {YT: [patch("Hide ads", options=[OPTION]), patch("Spoof", default=False)],
                                      REDDIT: [patch("Hide ads", versions=())]})
        self.download(self.basic["cli"], "v1.18.1", None, "morphe-desktop-1.18.1-all.jar")
        with db.tx() as c:
            c.execute("UPDATE item SET patch_overrides = ? WHERE id = 1",
                      (json.dumps({"Hide ads": {"enabled": False}, "Gone": {"enabled": True}}),))

    def download(self, upstream_id, tag, bundle_info, file_name="patches-1.mpp"):
        d = upstream.tag_dir(upstream_id, tag)
        d.mkdir(parents=True, exist_ok=True)
        (d / file_name).write_bytes(b"x")
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag = ? WHERE id = ?", (tag, upstream_id))
            for package, patches in (bundle_info or {}).items():
                c.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (?, ?, ?, ?)",
                          (upstream_id, tag, package, json.dumps(patches)))

    def set_item(self, iid, **cols):
        with db.tx() as c:   # k from the test's literal keywords
            c.execute(f"UPDATE item SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", (*cols.values(), iid))

    def bundles(self, *entries):
        """Fake catalog.app_bundles: every package gets these bundle entries."""
        rows = [dict({"host": "github", "repo": "r", "engine": "morphe", "selectable": True,
                      "not_selectable_reason": None, "patches": []}, **e) for e in entries]
        return self.enterContext(mock.patch.object(catalog, "app_bundles", side_effect=lambda pkg: rows))

    def jobs_of(self, kind):
        return [(json.loads(r["target"]), r["trigger"]) for r in
                db.conn().execute("SELECT target, trigger FROM jobs WHERE kind = ? ORDER BY id", (kind,))]

    def http_status(self, fn, *args):
        try:
            fn(*args)
        except server.HttpError as e:
            return e.status, e.code
        return None


class VersionTest(AppsCase):
    def test_want_version_modes(self):
        self.assertEqual((apps.want_version(1), apps.job_input(1)["version"]), ("20.13.41", {"value": "20.13.41", "near": False}))
        self.set_item(1, version_mode="pinned", version_pin="20.12.46", allow_nearest=1)
        self.assertEqual((apps.want_version(1), apps.job_input(1)["version"]), ("20.12.46", {"value": "20.12.46", "near": True}))
        with db.tx() as c:
            c.execute("UPDATE app SET apkmirror_versions = '[\"21.1.0\",\"20.13.41\"]' WHERE package IN (?, ?)", (YT, REDDIT))
        self.set_item(1, version_mode="latest", version_pin=None, allow_nearest=0)
        self.assertEqual((apps.want_version(1), apps.job_input(1)["version"]), ("21.1.0", {"value": None, "near": False}))
        # auto with an empty compatible list (Reddit: patch for any version) behaves like latest
        self.assertEqual((apps.want_version(2), apps.job_input(2)["version"]), ("21.1.0", {"value": None, "near": False}))

    def test_job_json_has_version_value_not_mode(self):
        j = apps.job_input(1)
        self.assertEqual(set(j), set(JOB_FIXTURE) - {"job_id", "work_dir"})
        self.assertEqual(set(j["version"]), {"value", "near"})
        self.assertEqual(set(j["apkmirror"]), set(JOB_FIXTURE["apkmirror"]))
        self.assertEqual(j["options_file_json"], [{"patches": {"Hide ads": {"enabled": False}}}])
        self.assertEqual((j["keystore"], j["upstream_tag"], j["cli_tag"]), ("morphe.keystore", "v1", "v1.18.1"))
        self.assertTrue(j["cli_jar"].endswith("morphe-desktop-1.18.1-all.jar"))
        self.assertEqual(apps.stale_overrides(1), ["Gone"])
        self.assertTrue(j["apkmirror"]["versions_due"])
        with db.tx() as c:
            c.execute("UPDATE app SET apkmirror_checked_at = ? WHERE package = ?", (db.utcnow(), YT))
        self.assertFalse(apps.job_input(1)["apkmirror"]["versions_due"])
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info WHERE package = ?", (YT,))
        with self.assertRaisesRegex(catalog.UpstreamMissing, f"patch list missing for {YT} at v1"):
            apps.job_input(1)
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag = NULL WHERE id = ?", (self.basic["cli"],))
        with self.assertRaisesRegex(catalog.UpstreamMissing, "CLI upstream"):
            apps.job_input(2)

    def test_version_settings_validation(self):
        bad = ({"mode": "pinned"}, {"mode": "auto", "allow_nearest": "x"}, {"mode": "never"}, {"mode": "auto", "x": 1})
        for v in bad:
            self.assertEqual(self.http_status(apps.update_settings, 1, {"version": v})[0], 400, v)
        s = apps.update_settings(1, {"version": {"mode": "pinned", "pin": "20.12.46"}})["settings"]["version"]
        self.assertEqual((s["mode"], s["pin"], s["allow_nearest"]), ("pinned", "20.12.46", False))
        s = apps.update_settings(1, {"version": {"mode": "latest", "pin": "x", "allow_nearest": True}})["settings"]["version"]
        self.assertEqual((s["mode"], s["pin"], s["allow_nearest"], s["supported"]), ("latest", None, True, ["20.13.41", "20.12.46"]))
        self.assertEqual(self.http_status(apps.update_settings, 1, {"download": {}})[0], 400)


class NeedsBuildTest(AppsCase):
    def built(self, iid=1, status="success"):
        with db.tx() as c:
            jid = c.execute("INSERT INTO jobs(kind, status, trigger, target, options, created_at) VALUES "
                            "('build', ?, 'manual', ?, '{}', 'now')", (status, json.dumps({"item_id": iid}))).lastrowid
            c.execute("INSERT INTO build_jobs(job_id, item_id, app_version, inputs_sha) VALUES (?, ?, '20.13.41', ?)",
                      (jid, iid, apps.inputs_sha(iid)))
            if status == "success":
                c.execute("INSERT INTO artifacts(job_id, item_id, variant, abi, path, created_at) "
                          "VALUES (?, ?, 'universal', 'universal', '/x', 'now')", (jid, iid))

    def flips(self, change, undo):
        change()
        flipped = build.needs_build(1)
        undo()
        self.assertFalse(build.needs_build(1))
        return flipped

    def test_needs_build_matrix(self):
        self.assertTrue(build.needs_build(1))   # never built
        self.built()
        self.assertFalse(build.needs_build(1))
        tag = lambda uid, t: lambda: db.conn().execute("UPDATE upstream SET downloaded_tag = ? WHERE id = ?", (t, uid))  # noqa: E731
        col = lambda k, v: lambda: self.set_item(1, **{k: v})  # noqa: E731
        apkm = lambda v: lambda: db.conn().execute("UPDATE app SET apkmirror_versions = ? WHERE package = ?", (v, YT))  # noqa: E731
        official = add_bundle(db.conn(), "o/other-patches")
        cases = {
            "bundle tag": (tag(self.up, "v2"), tag(self.up, "v1"), True),
            "cli tag": (tag(self.basic["cli"], "v2"), tag(self.basic["cli"], "v1.18.1"), True),
            "pin": (lambda: self.set_item(1, version_mode="pinned", version_pin="20.12.46"),
                    lambda: self.set_item(1, version_mode="auto", version_pin=None), True),
            "override": (col("patch_overrides", '{"Hide ads":{"enabled":true},"Gone":{"enabled":true}}'),
                         col("patch_overrides", '{"Hide ads":{"enabled":false},"Gone":{"enabled":true}}'), True),
            "stale override only": (col("patch_overrides", '{"Hide ads":{"enabled":false}}'),
                                    col("patch_overrides", '{"Hide ads":{"enabled":false},"Gone":{"enabled":true}}'), False),
            "bundle switch": (col("bundle_id", official), col("bundle_id", self.builtin), True),
            "auto: new APKMirror version": (apkm('["21.0.0"]'), apkm(None), False),
        }
        for name, (change, undo, want) in cases.items():
            self.assertIs(self.flips(change, undo), want, name)
        self.set_item(1, version_mode="latest")
        db.conn().execute("DELETE FROM artifacts")
        db.conn().execute("DELETE FROM build_jobs")
        self.built()
        self.assertIs(self.flips(apkm('["21.0.0"]'), apkm(None)), True, "latest: new APKMirror version")
        self.built(status="failed")
        self.assertTrue(build.needs_build(1))   # last finished build failed
        self.built()
        db.conn().execute("DELETE FROM artifacts")
        self.assertTrue(build.needs_build(1))   # artifacts cleaned up


class UpdateRunTest(AppsCase):
    def setUp(self):
        super().setUp()
        with db.tx() as c:
            c.execute("UPDATE app SET apkmirror_list_url = 'https://www.apkmirror.com/uploads/?appcategory=x' "
                      "WHERE package IN (?, ?)", (YT, REDDIT))
        (self.data / "work").mkdir(exist_ok=True)
        self.pulls = []
        self.enterContext(mock.patch.object(apklocate, "throttle"))
        self.locate = self.enterContext(mock.patch.object(apklocate, "locate"))

    def run_job(self, target, pull=None):
        self.enterContext(mock.patch.object(upstream, "pull", side_effect=pull or (lambda uid, log, job_id=None: self.pulls.append(uid))))
        job = SimpleNamespace(id=9, target=target, options={"notify": False}, trigger="schedule", schedule_id=None,
                              cancel_event=threading.Event())
        log = FakeLog()
        try:
            apps.run_update(job, log)
            return log, None
        except jobs.JobFailed as e:
            return log, e

    def test_update_run_continues_after_catalog_failure(self):
        self.enterContext(mock.patch.object(catalog, "refresh", side_effect=catalog.CatalogRefreshFailed("official: http 500")))
        self.enterContext(mock.patch.object(apps, "CHECK_VERSIONS", str(CHECK_FIX / "check-fail.sh")))
        self.set_item(2, version_mode="latest")

        def pull(uid, log, job_id=None):
            self.pulls.append(uid)
            if uid == self.up:
                raise jobs.JobFailed("no matching asset")
            return False
        log, err = self.run_job({"all": True}, pull)
        self.assertEqual(sorted(self.pulls), sorted({self.up, self.basic["cli"]}))   # disabled item 3 not in scope
        self.assertEqual(self.jobs_of("build"), [({"item_id": 1}, "schedule"), ({"item_id": 2}, "schedule")])
        self.assertRegex(err.reason, rf"^3 steps failed: catalog: official: http 500; upstream {self.up}: no matching "
                                     rf"asset; version check {REDDIT}: version check failed: \[-\] APKMirror list page fetch")
        e = db.conn().execute("SELECT apkm_error FROM app WHERE package = ?", (REDDIT,)).fetchone()[0]
        self.assertIn("fetch failed", e)
        self.locate.assert_not_called()

    def test_update_run_skips_app_without_url(self):
        with db.tx() as c:
            c.execute("UPDATE app SET apkmirror_list_url = NULL WHERE package = ?", (YT,))
        log, err = self.run_job({"item_id": 1})
        self.assertIsNone(err)
        self.locate.assert_called_once()
        self.assertEqual(self.locate.call_args.args[0], YT)
        self.assertEqual(self.jobs_of("build"), [])
        self.assertTrue(any("no download URL" in line for line in log.lines))

        def found(package, log, cancel_event):
            db.conn().execute("UPDATE app SET apkmirror_list_url = 'https://www.apkmirror.com/uploads/?appcategory=y', "
                              "apkm_state = 'found' WHERE package = ?", (package,))
        self.locate.side_effect = found
        self.run_job({"item_id": 1})
        self.assertEqual(self.jobs_of("build"), [({"item_id": 1}, "schedule")])
        self.set_item(1, enabled=0)
        self.assertEqual(self.run_job({"item_id": 1})[1].reason, "app 1 is disabled")


class EnableTest(AppsCase):
    def test_enable_new_and_reenable_keep_overrides(self):
        self.bundles({"bundle_id": self.builtin, "patches": [{"name": "Hide ads"}, {"name": "Spoof"}]})
        o = apps.enable("com.example.app", self.builtin)
        r = db.conn().execute("SELECT * FROM item WHERE id = ?", (o["id"],)).fetchone()
        self.assertEqual((r["enabled"], r["version_mode"], json.loads(r["variants"]), r["engine_options"]),
                         (1, "auto", catalog.default_variants(), None))
        self.assertEqual(self.jobs_of("upstream"), [({"item_id": o["id"]}, "manual")])
        self.assertEqual(self.http_status(apps.enable, "com.example.app", self.builtin), (409, "already_enabled"))
        self.set_item(1, enabled=0)
        again = apps.enable(YT, self.builtin, {"Spoof": True})
        self.assertEqual(again["id"], 1)
        stored = json.loads(db.conn().execute("SELECT patch_overrides FROM item WHERE id = 1").fetchone()[0])
        self.assertEqual(stored, {"Gone": {"enabled": True}, "Hide ads": {"enabled": False}, "Spoof": {"enabled": True}})
        apps.disable(1)
        self.assertEqual(db.conn().execute("SELECT enabled, patch_overrides FROM item WHERE id = 1").fetchone()[0], 0)
        self.assertEqual(self.http_status(apps.check_update, 1), (409, "app_disabled"))
        self.assertEqual(self.http_status(apps.rebuild, 1), (409, "app_disabled"))

    def test_enable_with_unknown_patch_is_400(self):
        self.bundles({"bundle_id": self.builtin, "patches": [{"name": "Hide ads"}]})
        self.assertEqual(self.http_status(apps.enable, "com.example.app", self.builtin, {"Nope": True}), (400, "patch_unknown"))
        self.assertEqual(self.http_status(apps.enable, "com.example.app", self.builtin, {"Hide ads": "yes"})[0], 400)
        self.assertIsNone(db.conn().execute("SELECT 1 FROM item WHERE package = 'com.example.app'").fetchone())
        self.assertEqual(self.http_status(apps.enable, "com.example.app", 999), (404, None))
        self.bundles({"bundle_id": self.builtin, "selectable": False, "not_selectable_reason": "GitLab releases not supported"})
        self.assertEqual(self.http_status(apps.enable, "com.example.app", self.builtin), (409, "bundle_not_selectable"))

    def test_bundle_change_keeps_same_name_overrides(self):
        other = add_bundle(db.conn(), "o/other-patches")
        self.bundles({"bundle_id": self.builtin}, {"bundle_id": other, "patches": [{"name": "Hide ads"}]})
        o = apps.update_settings(1, {"bundle_id": other})
        new_up = db.conn().execute("SELECT upstream_id FROM bundle WHERE id = ?", (other,)).fetchone()[0]
        self.assertIsNotNone(new_up)
        self.download(new_up, "v9", {YT: [patch("Hide ads")]})
        self.assertEqual(o["bundle"]["bundle_id"], other)
        self.assertEqual(json.loads(db.conn().execute("SELECT patch_overrides FROM item WHERE id = 1").fetchone()[0]),
                         {"Hide ads": {"enabled": False}, "Gone": {"enabled": True}})
        self.assertEqual(apps.get(1)["settings"]["stale_overrides"], ["Gone"])
        self.assertEqual([(p["name"], p["enabled"]) for p in apps.get_patches(1)["patches"]], [("Hide ads", False)])
        self.assertEqual(self.jobs_of("upstream"), [({"item_id": 1}, "manual")])
        third = add_bundle(db.conn(), "o/third-patches")
        self.assertEqual(self.http_status(apps.update_settings, 1, {"bundle_id": third}), (409, "bundle_unsupported"))
        self.assertEqual(self.http_status(apps.update_settings, 1, {"bundle_id": 999}), (404, None))


class PatchesTest(AppsCase):
    def test_put_patches_validates_types(self):
        b = apps.get_patches(1)
        self.assertEqual((b["tag"], b["files"], b["unavailable"], b["stale_overrides"]), ("v1", ["patches-1.mpp"], False, ["Gone"]))
        self.assertEqual([(p["name"], p["enabled"]) for p in b["patches"]], [("Hide ads", False), ("Spoof", False)])
        self.assertEqual(b["patches"][0]["options"][0]["value"], "full")
        ok = {"Hide ads": {"enabled": True, "options": {"mode": "lite"}}}
        apps.put_patches(1, {"overrides": ok})
        stored = json.loads(db.conn().execute("SELECT patch_overrides FROM item WHERE id = 1").fetchone()[0])
        self.assertEqual(stored, {"Gone": {"enabled": True}, "Hide ads": {"enabled": True, "options": {"mode": "lite"}}})
        bads = [{"Nope": {"enabled": True}}, {"Gone": {"enabled": False}}, {"Hide ads": {"options": {"nokey": 1}}},
                {"Hide ads": {"options": {"mode": 4}}}, {"Hide ads": {"enabled": "yes"}}, {"Hide ads": {"extra": 1}}]
        for bad in bads:
            self.assertEqual(self.http_status(apps.put_patches, 1, {"overrides": bad})[0], 400, bad)
        self.assertTrue(apps._type_ok("kotlin.collections.List<kotlin.String>", ["a"]))
        self.assertFalse(apps._type_ok("kotlin.Boolean", "true"))
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info WHERE package = ?", (REDDIT,))
        self.assertEqual(apps.get_patches(2)["unavailable"], True)
        self.assertEqual(self.http_status(apps.put_patches, 2, {"overrides": {}})[0], 409)


XV = {"name": "arm64-v8a", "abi": "arm64-v8a", "exclude_splits": None, "strip_libs": None,
      "download": {"kind": "apkmirror", "type": "bundle", "arch": "arm64-v8a", "dpi": "320-640dpi",
                   "minver": "Android 11+", "url": None}}


class ModuleTest(AppsCase):
    def test_module_bundle_variant_and_engine_rules(self):
        c = db.conn()
        mod_up = c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('module','gnadgnaoh/NexAlloy','stable')").lastrowid
        ls = add_bundle(c, "gnadgnaoh/NexAlloy", origin="custom", engine="lspatch", asset_regex=".*", upstream_id=mod_up)
        npatch = add_bundle(c, "gnadgnaoh/NexAlloy", origin="custom", engine="npatch", asset_regex=".*", host="gitlab")
        iid = add_item(c, "com.facebook.katana", ls, variants=json.dumps([XV]),
                       engine_options='{"sigbypass":3,"injectdex":true}')
        put = lambda body: self.http_status(apps.update_settings, iid, body)  # noqa: E731
        for eo in (None, {"sigbypass": 1}, {"sigbypass": 4, "injectdex": False}, {"sigbypass": True, "injectdex": False}, "x"):
            self.assertEqual(put({"engine_options": eo})[0], 400, eo)
        self.assertIsNone(put({"engine_options": {"sigbypass": 2, "injectdex": False}}))
        self.assertEqual(put({"variants": [dict(XV, strip_libs="arm64-v8a")]})[0], 400)
        self.assertEqual(put({"variants": [dict(XV, download=dict(XV["download"], type="bundle_extract"))]})[0], 400)
        for arch in ("-v", ".x"):
            self.assertEqual(put({"variants": [dict(XV, download=dict(XV["download"], arch=arch))]})[0], 400, arch)
        self.assertIsNone(put({"version": {"mode": "auto"}}))   # module engines take auto now (= latest)
        self.assertEqual(self.http_status(apps.update_settings, 1, {"engine_options": {"sigbypass": 0, "injectdex": False}})[0], 400)
        self.assertEqual(self.http_status(apps.get_patches, iid)[0], 409)
        self.download(mod_up, "v1.1", None, "NexAlloy-release-v1.1.apk")
        lsp = c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('cli','JingMatrix/LSPatch','stable')").lastrowid
        self.download(lsp, "v1.2", None, "lspatch-v1.2-487-release.jar")
        j = apps.job_input(iid)
        self.assertEqual((j["engine"], j["engine_options"], j["keystore"], j["options_file_json"]),
                         ("lspatch", {"sigbypass": 2, "injectdex": False}, "lspatch-signing.p12", None))
        self.assertEqual([Path(p).name for p in j["patches_files"]], ["NexAlloy-release-v1.1.apk"])
        # switching a patches app onto a module bundle resets engine options and clears lib stripping
        self.bundles({"bundle_id": ls, "engine": "lspatch"}, {"bundle_id": npatch, "engine": "npatch"})
        s = apps.update_settings(1, {"bundle_id": ls})["settings"]
        self.assertEqual(s["engine_options"], {"sigbypass": 0, "injectdex": False})
        self.assertTrue(all(v["strip_libs"] is None for v in s["variants"]))
