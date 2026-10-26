"""Release check and pull tests (spec catalog §5, §9, §10)."""
import json
import os
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from webui import db, jobs, settings, upstream
from webui.tests.catalog_rows import CatalogCase, FakeLog
from webui.tests.helpers import run_server

FIX = Path(__file__).parent / "fixtures" / "catalog"
API = upstream.API


def fix(name):
    return json.loads((FIX / name).read_text())


class UpstreamCase(CatalogCase):
    def setUp(self):
        super().setUp()
        self.cli = self.basic["cli"]
        self.pat = db.conn().execute("SELECT upstream_id FROM bundle WHERE id = ?", (self.basic["builtin"],)).fetchone()[0]

    def add_prerelease(self):
        with db.tx() as c:
            return c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('patches','MorpheApp/morphe-patches','prerelease')").lastrowid


class CheckTest(UpstreamCase):
    def test_stable_pick_skips_dev_and_asc(self):
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, fix("release-stable.json"), {})})
        row = upstream.check(self.pat)
        self.assertEqual((row["latest_tag"], row["check_error"]), ("v1.47.0", None))
        rel = fix("release-stable.json")
        self.assertEqual([a["name"] for a in upstream.match_assets(row, rel)], ["patches-1.47.0.mpp"])

    def test_prerelease_newest_non_draft(self):
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases?per_page=10": (200, fix("releases-list.json"), {})})
        self.assertEqual(upstream.check(self.add_prerelease())["latest_tag"], "v1.48.0-dev.3")

    def test_prerelease_when_stable_is_newer(self):
        rels = fix("releases-list.json")
        rels[1]["published_at"] = "2026-10-16T00:00:00Z"
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases?per_page=10": (200, rels, {})})
        self.assertEqual(upstream.check(self.add_prerelease())["latest_tag"], "v1.47.0")

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

    def test_401_is_token_rejected(self):
        self.http({f"{API}/repos/MorpheApp/morphe-desktop/releases/latest": (401, {}, {})})
        with self.assertRaises(upstream.TokenRejected):
            upstream.check(self.cli)
        self.assertEqual(upstream._row(self.cli)["check_error"], "token rejected")

    def test_429_message(self):
        self.http({f"{API}/repos/MorpheApp/morphe-desktop/releases/latest": (429, {}, {"x-ratelimit-reset": "1792500000"})})
        with self.assertRaises(upstream.RateLimited):
            upstream.check(self.cli)
        self.assertEqual(upstream._row(self.cli)["check_error"], "rate limited until 2026-10-20T12:40:00Z")

    def test_new_version_event_once(self):
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, fix("release-stable.json"), {})})
        upstream.check(self.pat)
        upstream.check(self.pat)
        evs = [e for e in self.emitted if e[0] == "upstream.new_version"]
        self.assertEqual(evs, [("upstream.new_version", {"upstream_id": self.pat, "tag": "v1.47.0", "item_ids": [1, 2], "job_id": None})])

    def test_token_header(self):
        f = self.http({})
        with mock.patch.object(settings, "github_token", return_value="tok"):
            upstream.check(self.pat)
        self.assertEqual(f.calls[0][1]["Authorization"], "Bearer tok")


CLI_REL = {"tag_name": "v1.18.1", "published_at": "2026-10-01T00:00:00Z", "draft": False,
           "assets": [{"name": "morphe-desktop-1.18.1-all.jar", "size": 5, "browser_download_url": "https://dl.invalid/cli.jar"}]}


class PullTest(UpstreamCase):
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
        self.assertTrue(upstream.pull(self.pat, self.log))
        self.assertTrue((self.data / "upstream" / str(self.pat) / "v1.47.0" / "patches-1.47.0.mpp").is_file())
        self.assertEqual(upstream._row(self.pat)["downloaded_tag"], "v1.47.0")
        self.assertEqual(upstream._row(self.cli)["downloaded_tag"], "v1.18.1")
        self.assertEqual(self.inspected, [(self.pat, "v1.47.0")])

    def test_size_mismatch_keeps_previous_tag(self):
        self.routes()
        upstream.pull(self.pat, self.log)
        self.routes(self.new_release(size=99))
        with self.assertRaises(jobs.JobFailed):
            upstream.pull(self.pat, self.log)
        base = self.data / "upstream" / str(self.pat)
        self.assertEqual(sorted(p.name for p in base.iterdir()), ["v1.47.0"])
        self.assertEqual(upstream._row(self.pat)["downloaded_tag"], "v1.47.0")
        self.assertTrue(upstream._row(self.pat)["check_error"].startswith("download failed"))

    def test_digest_mismatch(self):
        self.routes(self.new_release(digest="sha256:" + "0" * 64))
        with self.assertRaises(jobs.JobFailed):
            upstream.pull(self.pat, self.log)
        self.assertIsNone(upstream._row(self.pat)["downloaded_tag"])

    def old_tag_dirs(self):
        base = self.data / "upstream" / str(self.pat)
        for n, name in enumerate(("v1", "v2")):
            (base / name).mkdir(parents=True)
            os.utime(base / name, (1000 + n, 1000 + n))
        return base

    def test_keeps_two_newest_tag_dirs(self):
        base = self.old_tag_dirs()
        self.routes()
        upstream.pull(self.pat, self.log)
        self.assertEqual(sorted(p.name for p in base.iterdir()), ["v1.47.0", "v2"])

    def test_prune_deletes_bundle_info_of_pruned_tags(self):
        self.old_tag_dirs()
        with db.tx() as c:
            for tag in ("v1", "v2", "v1.47.0"):
                c.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (?,?,'com.reddit.frontpage','[]')",
                          (self.pat, tag))
        self.routes()
        upstream.pull(self.pat, self.log)
        tags = {r[0] for r in db.conn().execute("SELECT tag FROM bundle_info WHERE upstream_id = ?", (self.pat,))}
        self.assertEqual(tags, {"v2", "v1.47.0"})

    def test_up_to_date_when_inspected(self):
        self.routes()
        upstream.pull(self.pat, self.log)
        self.assertFalse(upstream.pull(self.pat, self.log))  # same tag, not inspected yet: inspect again
        with db.tx() as c:
            for pkg in ("com.google.android.youtube", "com.reddit.frontpage"):
                c.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (?,'v1.47.0',?,'[]')",
                          (self.pat, pkg))
        self.assertFalse(upstream.pull(self.pat, self.log))
        self.assertIn("[+] up to date", self.log.lines)
        self.assertEqual(len(self.inspected), 2)

    def test_stable_dev_filter_skips_project_rows(self):
        rel = {"assets": [{"name": "app-dev-release.apk"}, {"name": "app-release.apk"}]}
        row = {"kind": "project", "channel": "stable", "asset_regex": None}
        self.assertEqual([a["name"] for a in upstream.match_assets(row, rel)], ["app-dev-release.apk", "app-release.apk"])
        self.assertEqual([a["name"] for a in upstream.match_assets(dict(row, kind="module"), rel)], ["app-release.apk"])

    def test_affected_items_through_bundle_cli_and_projects(self):
        with db.tx() as c:  # item 3 (YT Music) is disabled
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (9,'project','MorpheApp/MicroG-RE','stable')")
            c.execute("INSERT INTO project(id, upstream_id, package) VALUES (1,9,'app.revanced.android.gms')")
            c.execute("INSERT INTO item_dependency(item_id, project_id) VALUES (3,1),(1,1)")
        self.assertEqual(upstream.affected_items(9), [1])
        self.assertEqual(upstream.affected_items(self.pat), [1, 2])
        self.assertEqual(upstream.affected_items(self.cli), [1, 2])

    def test_handler_event_carries_job_id(self):
        self.routes()
        upstream._handler(mock.Mock(id=77, target={"upstream_id": self.pat}, options={}), self.log)
        evs = [pl for t, pl in self.emitted if t == "upstream.new_version" and pl["upstream_id"] == self.pat]
        self.assertEqual([e["job_id"] for e in evs], [77])


BODY = b"0123456789"


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
                self.send_header("Content-Length", "0" if target else str(len(BODY)))
                self.end_headers()
                if not target:
                    self.wfile.write(BODY)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        run_server(self, srv)
        return srv, seen

    def url(self, srv, path):
        return f"http://127.0.0.1:{srv.server_address[1]}{path}"

    def test_cross_host_redirect_drops_authorization(self):
        b, b_seen = self.serve()
        a, _ = self.serve(lambda s: self.url(b, "/x"))
        status, _, _, _ = upstream.http_get(self.url(a, "/a"), upstream.gh_headers("SECRET"))
        self.assertEqual(status, 200)
        self.assertIn("/x", b_seen)
        self.assertIsNone(b_seen["/x"])

    def test_same_origin_redirect_keeps_authorization(self):
        a, seen = self.serve(lambda s: self.url(s, "/y"))
        status, _, _, _ = upstream.http_get(self.url(a, "/a"), upstream.gh_headers("SECRET"))
        self.assertEqual(status, 200)
        self.assertEqual(seen["/y"], "Bearer SECRET")

    def test_http_get_returns_final_url_and_caps_body(self):
        b, _ = self.serve()
        a, _ = self.serve(lambda s: self.url(b, "/x"))
        status, _, body, final = upstream.http_get(self.url(a, "/a"), max_bytes=len(BODY))
        self.assertEqual((status, body, final), (200, BODY, self.url(b, "/x")))
        with self.assertRaisesRegex(ValueError, f"/a: body over {len(BODY) - 1} bytes"):
            upstream.http_get(self.url(a, "/a"), max_bytes=len(BODY) - 1)

    def test_https_to_http_redirect_drops_authorization(self):
        req = urllib.request.Request("https://api.github.com/a", headers={"Authorization": "Bearer S"})
        new = upstream._SameOriginRedirect().redirect_request(req, None, 302, "Found", {}, "http://api.github.com/b")
        self.assertFalse(new.has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
