"""Catalog layer tests: feed grammar, refresh/apply, de-dup, patch lists, custom sources, icons (spec §2)."""
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from webui import catalog, db, server, upstream
from webui.catalog import Patch, Skip
from webui.tests.catalog_rows import CatalogCase, FakeLog, add_item

FIX = Path(__file__).parent / "fixtures" / "catalog"
MINI = (FIX / "bundles-mini.json").read_bytes()
PLIST = (FIX / "patches-list-mini.json").read_bytes()
RELEASE = json.loads((FIX / "release-stable.json").read_text())   # tag v1.47.0, one .mpp asset
FEED = catalog.FEEDS[0][1]
YT, MUSIC, REDDIT, CARDS = ("com.google.android.youtube", "com.google.android.apps.youtube.music",
                            "com.reddit.frontpage", "com.example.cards")


def gh_latest(repo):
    return f"{upstream.API}/repos/{repo}/releases/latest"


def list_url(repo):
    return catalog.LIST_URL.format(repo, "v1.47.0")


def feed_with(**changes):
    d = json.loads(MINI)
    d.update(changes)
    return json.dumps(d).encode()


class ParseTest(unittest.TestCase):
    def test_parse_official_accepts_all_dialects(self):
        cat = catalog.parse_official(MINI)
        by = {b.repo: b for b in cat.bundles}
        self.assertEqual(list(by), ["alice/yt-patches", "bob/reddit-patches", "group/sub/gl-patches", "carol/dev-patches"])
        alice = by["alice/yt-patches"]
        self.assertEqual(alice.apps, {YT: ("20.14.1", "20.12.46"), REDDIT: ("2025.10.0",)})   # dict compat, {} target
        self.assertEqual(alice.patches, (Patch("Hide ads", "No ads.", True, (YT,)), Patch("Spoof", None, False, (REDDIT,)),
                                         Patch("Universal theme", "Any app.", None, ())))   # both key pairs, universal
        self.assertEqual((alice.description, alice.stars, alice.updated_at), ("YouTube extras", 40, "2026-09-30T00:00:00Z"))
        self.assertEqual(by["bob/reddit-patches"].apps, {REDDIT: ("2025.12.0",)})   # versions[] + targets null
        self.assertIsNone(by["bob/reddit-patches"].updated_at)
        self.assertEqual(by["group/sub/gl-patches"].host, "gitlab")
        carol = by["carol/dev-patches"]
        self.assertEqual((carol.branch, carol.apps), ("dev", {CARDS: ()}))   # target without version
        self.assertEqual(cat.duplicates, ("alice/yt-patches",))
        self.assertEqual(cat.store[REDDIT].description, "Front page & more")
        self.assertEqual((cat.store[CARDS].icon_url, cat.store[CARDS].description), (None, None))

    def test_parse_official_rejects_bad_root(self):
        cases = {b"not json": "$: expected JSON", b"[]": "$: expected an object, got 'list'",
                 b'{"compatibilities": [], "store": {}}': "bundles: expected list, got 'NoneType'",
                 b'{"bundles": [], "compatibilities": [], "store": []}': "store: expected dict, got 'list'"}
        for raw, msg in cases.items():
            with self.assertRaises(catalog.CatalogInvalid, msg=raw) as cm:
                catalog.parse_official(raw)
            self.assertTrue(str(cm.exception).startswith(msg), str(cm.exception))


class FeedCase(CatalogCase):
    def setUp(self):
        super().setUp()
        self.log = FakeLog()

    def serve(self, feed=MINI, status=200, etag='"e1"', **routes):
        return self.http({FEED: (status, feed, {"etag": etag}),
                          gh_latest("MorpheApp/morphe-patches"): (200, RELEASE, {}),
                          list_url("MorpheApp/morphe-patches"): (200, PLIST, {}), **routes})

    def bundles(self):
        return {r["repo"]: dict(r) for r in db.conn().execute("SELECT * FROM bundle")}

    def packages(self, bundle_id):
        return {r[0]: json.loads(r[1]) for r in db.conn().execute(
            "SELECT package, versions FROM bundle_app WHERE bundle_id = ?", (bundle_id,))}


class RefreshTest(FeedCase):
    def test_parse_official_skips_bad_bundle_and_records_it(self):
        cat = catalog.parse_official(MINI)
        self.assertEqual(cat.skipped, (
            Skip("bundles[5]", "bad/compat", "bundles[5].patches[0].compatiblePackagesKey: expected an index < 5, got 99"),
            Skip("store.com.bad.store", None, "store.com.bad.store.name: expected a non-empty string, got ''")))
        bad_compat = json.loads(MINI)["compatibilities"]
        bad_compat[4] = [{"packageName": "not a package"}]   # only the bundle pointing here is skipped
        self.serve(feed_with(compatibilities=bad_compat))
        catalog.refresh(self.log)
        self.assertNotIn("carol/dev-patches", self.bundles())
        self.assertIn("alice/yt-patches", self.bundles())
        skipped = catalog.feed_status()["feeds"][0]["skipped"]
        self.assertEqual([(s["where"], s["repo"]) for s in skipped],
                         [("bundles[3]", "carol/dev-patches"), ("bundles[5]", "bad/compat"), ("store.com.bad.store", None)])
        self.assertIn("compatibilities[4][0].packageName: expected a package name", skipped[0]["error"])
        self.assertTrue(any(line.startswith("[!] catalog official: skipped bundles[5]") for line in self.log.lines))

    def test_refresh_keeps_last_good_copy(self):
        self.serve()
        catalog.refresh(self.log)
        before = self.bundles()
        self.assertEqual({r for r, b in before.items() if b["origin"] == "official" and b["listed"]},
                         {"alice/yt-patches", "bob/reddit-patches", "group/sub/gl-patches", "carol/dev-patches"})
        self.serve(feed=b"{", etag='"e2"')
        with self.assertRaisesRegex(catalog.CatalogRefreshFailed, r"^official: \$: expected JSON"):
            catalog.refresh(self.log)
        self.assertEqual(self.bundles(), before)
        st = db.conn().execute("SELECT etag, last_error FROM catalog_feed WHERE feed = 'official'").fetchone()
        self.assertEqual(st["etag"], '"e1"')
        self.assertTrue(st["last_error"].startswith("$: expected JSON"))
        bob = before["bob/reddit-patches"]["id"]
        db.conn().execute("DELETE FROM bundle_app WHERE bundle_id = ?", (bob,))
        f = self.serve(status=304, feed=b"")
        catalog.refresh(self.log)
        self.assertEqual(f.calls[0][1]["If-None-Match"], '"e1"')
        self.assertEqual(self.packages(bob), {})   # 304: nothing re-applied

    def test_apply_keeps_ids_and_chosen_bundles(self):
        self.serve()
        catalog.refresh(self.log)
        ids = {r: b["id"] for r, b in self.bundles().items()}
        with db.tx() as c:   # item 3 (disabled) now remembers bob
            c.execute("UPDATE item SET bundle_id = ? WHERE id = 3", (ids["bob/reddit-patches"],))
        d = json.loads(MINI)
        d["bundles"] = [b for b in d["bundles"] if b["repo"] not in ("bob/reddit-patches", "carol/dev-patches")]
        self.serve(json.dumps(d).encode(), etag='"e2"')
        catalog.refresh(self.log)
        after = self.bundles()
        self.assertEqual((after["bob/reddit-patches"]["id"], after["bob/reddit-patches"]["listed"]),
                         (ids["bob/reddit-patches"], 0))
        self.assertEqual(self.packages(ids["bob/reddit-patches"]), {REDDIT: ["2025.12.0"]})
        self.assertNotIn("carol/dev-patches", after)
        self.assertNotIn("o/official-patches", after)   # no longer chosen by any app
        self.assertEqual(after["alice/yt-patches"]["id"], ids["alice/yt-patches"])
        self.serve(etag='"e3"')
        catalog.refresh(self.log)
        self.assertEqual(self.bundles()["bob/reddit-patches"]["listed"], 1)

    def test_dedup_prefers_builtin_and_custom(self):
        d = json.loads(MINI)
        d["bundles"].append({"source": "github", "repo": "MorpheApp/morphe-patches", "name": "Morphe", "author": "m",
                             "stars": 900, "patches": [{"name": "Feed only", "compatiblePackagesKey": 1}]})
        self.serve(json.dumps(d).encode())
        catalog.refresh(self.log)
        rows = self.bundles()
        builtin = rows["MorpheApp/morphe-patches"]
        self.assertEqual((builtin["id"], builtin["origin"], builtin["stars"]), (self.basic["builtin"], "builtin", None))
        self.assertEqual(set(self.packages(builtin["id"])), {YT, MUSIC})   # from its patches-list.json, not the feed
        apps = {a["package"]: a for a in catalog.market_apps()}
        self.assertEqual(apps[YT]["origins"], ["builtin", "official"])
        self.assertEqual((apps[YT]["bundle_count"], apps[YT]["newest_version"]), (2, "20.14.1"))
        alice = rows["alice/yt-patches"]["id"]
        self.serve(json.dumps(d).encode(), etag='"e2"', **{gh_latest("alice/yt-patches"): (200, RELEASE, {}),
                                                         list_url("alice/yt-patches"): (200, PLIST, {})})
        self.assertEqual(catalog.add_custom("https://github.com/alice/yt-patches", "morphe"), alice)   # converted in place
        catalog.refresh(self.log)
        row = self.bundles()["alice/yt-patches"]
        self.assertEqual((row["id"], row["origin"], row["list_tag"]), (alice, "custom", "v1.47.0"))
        self.assertEqual(set(self.packages(alice)), {YT, MUSIC})
        self.assertEqual(catalog.app_bundles(YT)[:2][1]["origin"], "custom")


class SourceTest(FeedCase):
    def test_parse_patches_list_rows_and_universal(self):
        version, patches, apps, names, skipped = catalog.parse_patches_list(PLIST)
        self.assertEqual(version, "1.48.0")
        self.assertEqual([(p["name"], p["default_enabled"], p["packages"]) for p in patches],
                         [("Hide ads", True, [MUSIC, YT]), ("Spoof client", False, [YT]), ("Change package name", False, [])])
        self.assertEqual(apps, {YT: ["20.14.1", "20.12.46"], MUSIC: ["8.10.52"]})
        self.assertEqual(names, {YT: "YouTube", MUSIC: "YouTube Music"})
        self.assertEqual(skipped, [Skip("patches[3]", None, "patches[3].name: expected a non-empty string, got ''")])
        with self.assertRaises(catalog.CatalogInvalid):
            catalog.parse_patches_list(b'{"version": "1", "patches": {}}')
        self.serve()
        catalog.refresh(self.log)
        b = self.bundles()["MorpheApp/morphe-patches"]
        self.assertEqual(b["list_tag"], "v1.47.0")
        self.assertTrue(b["list_error"].startswith("skipped: patches[3].name"))
        builtin = catalog.app_bundles(YT)[0]
        self.assertEqual([(p["name"], p["default_enabled"]) for p in builtin["patches"]],
                         [("Change package name", False), ("Hide ads", True), ("Spoof client", False)])
        self.assertEqual(builtin["versions"], ["20.14.1", "20.12.46"])

    def test_refresh_clears_stale_list_error_on_unchanged_tag(self):
        self.serve()
        catalog.refresh(self.log)
        with db.tx() as c:
            c.execute("UPDATE bundle SET list_error = 'rate limited until 2026-10-28T14:53:05Z' WHERE repo = ?",
                      ("MorpheApp/morphe-patches",))
        catalog.refresh(self.log)
        self.assertTrue(self.bundles()["MorpheApp/morphe-patches"]["list_error"].startswith("skipped: patches[3].name"))

    def test_add_custom_module_rules(self):
        f = self.http({})
        url, rx = "https://github.com/revenge-mod/revenge-xposed", r"^app-release\.apk$"
        bad = [("lspatch", None, ["com.discord"]), ("lspatch", "(", ["com.discord"]), ("npatch", rx, []),
               ("npatch", rx, [f"com.a{i}" for i in range(21)]), ("lspatch", rx, ["not a package"]),
               ("lspatch", rx, ["com.discord", "com.discord"]), ("morphe", rx, []), ("xposed", rx, ["com.discord"])]
        for engine, regex, pkgs in bad:
            with self.assertRaises(server.HttpError, msg=(engine, regex, pkgs)) as cm:
                catalog.add_custom(url, engine, regex, pkgs)
            self.assertEqual(cm.exception.status, 400)
        bid = catalog.add_custom(url, "lspatch", rx, ["com.discord"])
        self.assertEqual(f.calls, [])   # module sources need no network on add
        b = self.bundles()["revenge-mod/revenge-xposed"]
        kind = db.conn().execute("SELECT kind FROM upstream WHERE id = ?", (b["upstream_id"],)).fetchone()[0]
        self.assertEqual((b["origin"], b["engine"], b["asset_regex"], kind), ("custom", "lspatch", rx, "module"))
        self.assertEqual(self.packages(bid), {"com.discord": []})
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM bundle_patch WHERE bundle_id = ?", (bid,)).fetchone()[0], 0)
        self.assertEqual(catalog.list_custom()[0]["packages"], ["com.discord"])
        with self.assertRaises(server.HttpError) as cm:
            catalog.add_custom(url, "lspatch", rx, ["com.discord"])
        self.assertEqual(cm.exception.status, 409)
        with db.tx() as c:
            item = add_item(c, "com.discord", bid, enabled=0)
        with self.assertRaises(server.HttpError) as cm:
            catalog.delete_custom(bid)
        self.assertEqual((cm.exception.status, cm.exception.code, cm.exception.extra), (409, "source_in_use", {"item_ids": [item]}))
        db.conn().execute("DELETE FROM item WHERE id = ?", (item,))
        catalog.delete_custom(bid)
        self.assertNotIn("revenge-mod/revenge-xposed", self.bundles())
        self.assertIsNone(db.conn().execute("SELECT 1 FROM upstream WHERE id = ?", (b["upstream_id"],)).fetchone())

    def test_add_custom_without_patches_list_is_refused(self):
        self.http({gh_latest("cust/morphe-x"): (200, RELEASE, {})})
        with self.assertRaises(server.HttpError) as cm:
            catalog.add_custom("https://github.com/cust/morphe-x", "morphe")
        self.assertEqual((cm.exception.status, cm.exception.message), (400, "cust/morphe-x has no patches-list.json at v1.47.0"))
        self.assertNotIn("cust/morphe-x", self.bundles())
        self.assertIsNone(db.conn().execute("SELECT 1 FROM upstream WHERE repo = 'cust/morphe-x'").fetchone())


PNG = b"\x89PNG fake"


class IconTest(FeedCase):
    def setUp(self):
        super().setUp()
        self.out = self.enterContext(contextlib.redirect_stdout(io.StringIO()))   # the server log

    def icon(self, pkg):
        try:
            return self.call(catalog.get_icon, method="GET", pkg=pkg)
        except server.HttpError as e:
            return e.status

    def set_icon_url(self, pkg, url):
        db.conn().execute("UPDATE store_app SET icon_url = ? WHERE package = ?", (url, pkg))

    def tried(self, pkg):
        return db.conn().execute("SELECT icon_tried_url FROM store_app WHERE package = ?", (pkg,)).fetchone()[0]

    def test_icon_fetch_refuses_foreign_host_redirect_and_size(self):
        ok = "https://play-lh.googleusercontent.com/reddit=s128-rw"
        f = self.http({ok: (200, PNG, {"content-type": "image/png"}, "https://evil.example/x.png")})
        self.set_icon_url(REDDIT, "https://evil.example/x.png")
        self.assertEqual(self.icon(REDDIT), 404)
        self.assertEqual((f.calls, self.tried(REDDIT)), ([], "https://evil.example/x.png"))   # refused before a request
        self.assertIn("icon com.reddit.frontpage: https://evil.example/x.png: expected https on", self.out.getvalue())
        self.set_icon_url(REDDIT, ok)
        self.assertEqual(self.icon(REDDIT), 404)   # redirect off the allowlist
        self.assertEqual(self.tried(REDDIT), ok)
        self.assertEqual(self.icon(REDDIT), 404)
        self.assertEqual(len(f.calls), 1)   # a failure is not retried for the same URL

        def too_big(url, headers=None, timeout=30, max_bytes=None):
            self.assertEqual(max_bytes, catalog.ICON_MAX_BYTES)
            raise ValueError(f"{url}: body over {max_bytes} bytes")
        self.enterContext(mock.patch.object(upstream, "http_get", too_big))
        self.call(catalog.post_icon_refresh, pkg=REDDIT)
        self.assertEqual(self.icon(REDDIT), 404)

        page = catalog.PLAY_URL.format(YT)
        img = "https://play-lh.googleusercontent.com/yt=s256-rw"
        f = self.http({page: (200, b'<meta property="og:image" content="https://play-lh.googleusercontent.com/yt=s0-br30">', {}),
                       img: (200, PNG, {"content-type": "image/png"})})
        resp = self.icon(YT)
        self.assertIsInstance(resp, server.FileResponse)
        self.assertEqual(([u for u, _ in f.calls], resp.path.read_bytes()), ([page, img], PNG))
        self.assertEqual(self.icon("com.not.listed"), 404)


if __name__ == "__main__":
    unittest.main()
