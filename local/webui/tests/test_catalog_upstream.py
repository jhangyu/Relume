"""Release check and pull tests (spec catalog §5, §9, §10)."""
import json
import os
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from webui import db, jobs, settings, upstream
from webui.tests.catalog_rows import CatalogCase, FakeLog

FIX = Path(__file__).parent / "fixtures" / "catalog"
API = upstream.API


def fix(name):
    return json.loads((FIX / name).read_text())


class CheckTest(CatalogCase):
    def test_stable_pick_skips_dev_and_asc(self):
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, fix("release-stable.json"), {})})
        row = upstream.check(2)
        self.assertEqual((row["latest_tag"], row["check_error"]), ("v1.47.0", None))
        rel = fix("release-stable.json")
        self.assertEqual([a["name"] for a in upstream.match_assets(row, rel)], ["patches-1.47.0.mpp"])

    def test_prerelease_newest_non_draft(self):
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases?per_page=10": (200, fix("releases-list.json"), {})})
        self.assertEqual(upstream.check(3)["latest_tag"], "v1.48.0-dev.3")

    def test_prerelease_when_stable_is_newer(self):
        rels = fix("releases-list.json")
        rels[1]["published_at"] = "2026-10-16T00:00:00Z"
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases?per_page=10": (200, rels, {})})
        self.assertEqual(upstream.check(3)["latest_tag"], "v1.47.0")

    def test_tag_channel_url_and_no_matching_asset(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel, tag) VALUES (5,'patches','indrastorms/Dropped-Patches','tag','v1.4.2')")
        f = self.http({f"{API}/repos/indrastorms/Dropped-Patches/releases/tags/v1.4.2": (200, fix("release-tag.json"), {})})
        row = upstream.check(5)
        self.assertEqual(f.calls[0][0], f"{API}/repos/indrastorms/Dropped-Patches/releases/tags/v1.4.2")
        self.assertEqual(row["check_error"], "no matching asset")
        with db.tx() as c:
            c.execute(r"UPDATE upstream SET asset_regex='\.jar$' WHERE id=5")
        self.assertIsNone(upstream.check(5)["check_error"])

    def test_module_with_two_assets_is_ambiguous(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel, downloaded_tag) VALUES (6,'module','revenge-mod/revenge-xposed','stable','1800')")
        self.http({f"{API}/repos/revenge-mod/revenge-xposed/releases/latest": (200, fix("release-module-two.json"), {})})
        row = upstream.check(6)
        self.assertEqual(row["check_error"], "ambiguous module asset (2 matches)")
        self.assertEqual(row["downloaded_tag"], "1800")

    def test_401_stops_check_all(self):
        f = self.http({f"{API}/repos/MorpheApp/morphe-desktop/releases/latest": (401, {}, {})})
        rows = {r["id"]: r for r in upstream.check_all()}
        self.assertEqual(rows[1]["check_error"], "token rejected")
        self.assertEqual(len(f.calls), 1)
        self.assertIsNone(rows[2]["checked_at"])

    def test_429_message(self):
        self.http({f"{API}/repos/MorpheApp/morphe-desktop/releases/latest": (429, {}, {"x-ratelimit-reset": "1792500000"})})
        with self.assertRaises(upstream.RateLimited):
            upstream.check(1)
        self.assertEqual(upstream._row(1)["check_error"], "rate limited until 2026-10-20T12:40:00Z")

    def test_new_version_event_once(self):
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, fix("release-stable.json"), {})})
        upstream.check(2)
        upstream.check(2)
        evs = [e for e in self.emitted if e[0] == "upstream.new_version"]
        self.assertEqual(evs, [("upstream.new_version", {"upstream_id": 2, "tag": "v1.47.0", "item_ids": [1, 2], "job_id": None})])

    def test_token_header(self):
        f = self.http({})
        with mock.patch.object(settings, "github_token", return_value="tok"):
            upstream.check(2)
        self.assertEqual(f.calls[0][1]["Authorization"], "Bearer tok")


CLI_REL = {"tag_name": "v1.18.1", "published_at": "2026-10-01T00:00:00Z", "draft": False,
           "assets": [{"name": "morphe-desktop-1.18.1-all.jar", "size": 5, "browser_download_url": "https://dl.invalid/cli.jar"}]}


class PullTest(CatalogCase):
    def setUp(self):
        super().setUp()
        self.blobs = {"https://dl.invalid/patches-1.47.0.mpp": b"hello", "https://dl.invalid/cli.jar": b"hello",
                      "https://dl.invalid/new.mpp": b"hello!", "https://dl.invalid/microg.apk": b"hello"}
        self.enterContext(mock.patch.object(upstream, "http_download", self.fake_dl))
        self.inspected = []
        self.enterContext(mock.patch.object(upstream, "inspect_row", lambda uid, tag, log: self.inspected.append((uid, tag))))
        self.log = FakeLog()

    def fake_dl(self, url, dest, timeout=300):
        Path(dest).write_bytes(self.blobs[url])
        return len(self.blobs[url])

    def routes(self, patches=None):
        return self.http({f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, patches or fix("release-stable.json"), {}),
                          f"{API}/repos/MorpheApp/morphe-desktop/releases/latest": (200, CLI_REL, {})})

    def new_release(self, **asset):
        rel = {"tag_name": "v1.48.0", "published_at": "2026-10-12T00:00:00Z", "draft": False,
               "assets": [dict({"name": "patches-1.48.0.mpp", "size": 6, "browser_download_url": "https://dl.invalid/new.mpp"}, **asset)]}
        return rel

    def test_fresh_pull_pulls_cli_then_inspects(self):
        self.routes()
        upstream.pull(2, self.log)
        self.assertTrue((self.data / "upstream" / "2" / "v1.47.0" / "patches-1.47.0.mpp").is_file())
        self.assertEqual(upstream._row(2)["downloaded_tag"], "v1.47.0")
        self.assertEqual(upstream._row(1)["downloaded_tag"], "v1.18.1")
        self.assertEqual(self.inspected, [(2, "v1.47.0")])

    def test_size_mismatch_keeps_previous_tag(self):
        self.routes()
        upstream.pull(2, self.log)
        self.routes(self.new_release(size=99))
        with self.assertRaises(jobs.JobFailed):
            upstream.pull(2, self.log)
        base = self.data / "upstream" / "2"
        self.assertEqual(sorted(p.name for p in base.iterdir()), ["v1.47.0"])
        self.assertEqual(upstream._row(2)["downloaded_tag"], "v1.47.0")
        self.assertTrue(upstream._row(2)["check_error"].startswith("download failed"))

    def test_digest_mismatch(self):
        self.routes(self.new_release(digest="sha256:" + "0" * 64))
        with self.assertRaises(jobs.JobFailed):
            upstream.pull(2, self.log)
        self.assertIsNone(upstream._row(2)["downloaded_tag"])

    def test_keeps_two_newest_tag_dirs(self):
        base = self.data / "upstream" / "2"
        for n, name in enumerate(("v1", "v2")):
            (base / name).mkdir(parents=True)
            os.utime(base / name, (1000 + n, 1000 + n))
        self.routes()
        upstream.pull(2, self.log)
        self.assertEqual(sorted(p.name for p in base.iterdir()), ["v1.47.0", "v2"])

    def test_rebuild_enqueues_items_with_build_and_update_on_only(self):
        with db.tx() as c:  # item 1 both on, 2 Build off, 4 Update off, 3 is on another source
            c.execute("UPDATE item SET auto_update=1 WHERE id IN (1, 2)")
            c.execute("UPDATE item SET enabled=0 WHERE id=2")
        with db.tx() as c:
            c.execute("INSERT INTO app(package) VALUES ('com.tumblr')")
            c.execute("INSERT INTO item(id, source_id, package, key, position, variants, auto_update) VALUES (4,'morphe','com.tumblr','tumblr',9,'[]',0)")
        self.routes()
        upstream.pull(2, self.log, rebuild=True)
        rows = db.conn().execute("SELECT target, trigger FROM jobs WHERE kind='build'").fetchall()
        self.assertEqual([(json.loads(r["target"]), r["trigger"]) for r in rows], [({"item_id": 1}, "upstream")])
        upstream.pull(2, self.log, rebuild=True)  # same tag: no new builds
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM jobs WHERE kind='build'").fetchone()[0], 1)

    def queued_builds(self):
        return [json.loads(r[0])["item_id"] for r in db.conn().execute("SELECT target FROM jobs WHERE kind='build' ORDER BY id")]

    def test_new_cli_tag_rebuilds_switched_on_items_of_its_sources(self):
        with db.tx() as c:  # items 1 and 3 follow the CLI through two sources, 2 has Update off
            c.execute("UPDATE item SET auto_update=1 WHERE id IN (1, 3)")
        self.routes()
        upstream.pull(1, self.log, rebuild=True)
        self.assertEqual(self.queued_builds(), [1, 3])

    def test_new_dependency_project_tag_rebuilds_switched_on_dependents(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (9,'project','MorpheApp/MicroG-RE','stable')")
            c.execute("INSERT INTO project(id, upstream_id) VALUES (1, 9)")
            c.execute("INSERT INTO item_dependency(item_id, project_id) VALUES (1,1),(2,1),(3,1)")
            c.execute("UPDATE item SET auto_update=1 WHERE id IN (1, 3)")
            c.execute("UPDATE item SET enabled=0 WHERE id=3")
        rel = {"tag_name": "v7", "draft": False, "assets": [{"name": "microg.apk", "size": 5, "browser_download_url": "https://dl.invalid/microg.apk"}]}
        self.http({f"{API}/repos/MorpheApp/MicroG-RE/releases/latest": (200, rel, {})})
        with mock.patch.object(upstream.projects, "verify_release"), mock.patch.object(upstream.projects, "after_download"):
            upstream.pull(9, self.log, rebuild=True)
        self.assertEqual(self.queued_builds(), [1])

    def test_no_rebuild_flag(self):
        self.routes()
        upstream.pull(2, self.log)
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM jobs WHERE kind='build'").fetchone()[0], 0)

    def test_up_to_date_when_inspected(self):
        self.routes()
        upstream.pull(2, self.log)
        with db.tx() as c:
            for pkg in ("com.google.android.youtube", "com.reddit.frontpage"):
                c.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (2,'v1.47.0',?,'[]')", (pkg,))
        upstream.pull(2, self.log)
        self.assertIn("[+] up to date", self.log.lines)
        self.assertEqual(len(self.inspected), 1)

    def test_stable_dev_filter_skips_project_rows(self):
        rel = {"assets": [{"name": "app-dev-release.apk"}, {"name": "app-release.apk"}]}
        row = {"kind": "project", "channel": "stable", "asset_regex": None}
        self.assertEqual([a["name"] for a in upstream.match_assets(row, rel)], ["app-dev-release.apk", "app-release.apk"])
        self.assertEqual([a["name"] for a in upstream.match_assets(dict(row, kind="module"), rel)], ["app-release.apk"])

    def test_affected_items_through_projects(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (9,'project','MorpheApp/MicroG-RE','stable')")
            c.execute("INSERT INTO project(id, upstream_id, package) VALUES (1,9,'app.revanced.android.gms')")
            c.execute("INSERT INTO item_dependency(item_id, project_id) VALUES (3,1),(1,1)")
        self.assertEqual(upstream.affected_items(9), [1, 3])
        self.assertEqual(upstream.affected_items(2), [1, 2])

    def test_module_new_tag_rebuilds_that_source_only(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (6,'module','revenge-mod/revenge-xposed','stable'),(7,'cli','JingMatrix/LSPatch','stable')")
            c.execute("INSERT INTO source(id, name, upstream_id, cli_upstream_id, engine, keystore) VALUES ('revenge-lspatch','Revenge',6,7,'lspatch','lspatch-signing.p12')")
            c.execute("INSERT INTO app(package) VALUES ('com.discord')")
            c.execute("INSERT INTO item(id, source_id, package, key, group_id, position, variants, auto_update) VALUES (10,'revenge-lspatch','com.discord','discord',NULL,0,'[]',1)")
        rel = {"tag_name": "1801", "draft": False, "assets": [{"name": "app-release.apk", "size": 5, "browser_download_url": "https://dl.invalid/microg.apk"}]}
        self.http({f"{API}/repos/revenge-mod/revenge-xposed/releases/latest": (200, rel, {})})
        upstream.pull(6, self.log, rebuild=True)
        rows = db.conn().execute("SELECT target FROM jobs WHERE kind='build'").fetchall()
        self.assertEqual([json.loads(r["target"]) for r in rows], [{"item_id": 10}])
        self.assertEqual(self.inspected, [])

    def test_handler_event_carries_job_id(self):
        self.routes()
        upstream._handler(mock.Mock(id=77, target={"upstream_id": 2}, options={}), self.log)
        evs = [pl for t, pl in self.emitted if t == "upstream.new_version" and pl["upstream_id"] == 2]
        self.assertEqual([e["job_id"] for e in evs], [77])

    def test_post_pull(self):
        st, body = self.call(upstream.post_pull, json={"rebuild": True}, id=2)
        row = db.conn().execute("SELECT options FROM jobs WHERE id=?", (body["job_id"],)).fetchone()
        self.assertEqual(json.loads(row["options"])["rebuild"], True)
        self.assertEqual(self.status_of(upstream.post_pull, json={}, id=99), 404)



class RedirectAuthTest(unittest.TestCase):
    """The GitHub token never follows a redirect to another origin or scheme."""

    def serve(self, location=None):
        """Local server: answers 302 to `location` (a callable given the server) or 200; records headers per path."""
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                seen[self.path] = self.headers.get("Authorization")
                target = location(srv) if location and self.path == "/a" else None
                self.send_response(302 if target else 200)
                if target:
                    self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, seen

    def url(self, srv, path):
        return f"http://127.0.0.1:{srv.server_address[1]}{path}"

    def test_cross_host_redirect_drops_authorization(self):
        b, b_seen = self.serve()
        a, _ = self.serve(lambda s: self.url(b, "/x"))
        status, _, _ = upstream.http_get(self.url(a, "/a"), upstream.gh_headers("SECRET"))
        self.assertEqual(status, 200)
        self.assertIn("/x", b_seen)
        self.assertIsNone(b_seen["/x"])

    def test_same_origin_redirect_keeps_authorization(self):
        a, seen = self.serve(lambda s: self.url(s, "/y"))
        status, _, _ = upstream.http_get(self.url(a, "/a"), upstream.gh_headers("SECRET"))
        self.assertEqual(status, 200)
        self.assertEqual(seen["/y"], "Bearer SECRET")

    def test_https_to_http_redirect_drops_authorization(self):
        req = urllib.request.Request("https://api.github.com/a", headers={"Authorization": "Bearer S"})
        new = upstream._SameOriginRedirect().redirect_request(req, None, 302, "Found", {}, "http://api.github.com/b")
        self.assertFalse(new.has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
