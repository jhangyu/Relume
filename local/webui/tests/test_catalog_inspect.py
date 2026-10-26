"""Inspector tests: CLI output parser, isolated inspect run, re-inspect on pull (spec catalog §4, §5.2-5.3)."""
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from webui import db, upstream
from webui.tests.catalog_rows import CatalogCase, FakeLog
from webui.tests.test_catalog_upstream import API, fix

FIX = Path(__file__).parent / "fixtures" / "catalog"
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
        self.cli = self.basic["cli"]
        self.pat = db.conn().execute("SELECT upstream_id FROM bundle WHERE id = ?", (self.basic["builtin"],)).fetchone()[0]
        self.ptag = self.data / "upstream" / str(self.pat) / "v1"
        self.ptag.mkdir(parents=True)
        (self.ptag / "patches-1.mpp").write_bytes(b"x")
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag='v1' WHERE id=?", (self.pat,))
            c.execute("INSERT INTO bundle_info(upstream_id, tag, package, patches) VALUES (?,'v1',?,?)",
                      (self.pat, YT, json.dumps(upstream.merge_bundle(TEXT, "[]", YT))))

    def add_cli(self):
        d = self.data / "upstream" / str(self.cli) / "v1.18.1"
        d.mkdir(parents=True)
        (d / "morphe-desktop-1.18.1-all.jar").write_bytes(b"x")
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag='v1.18.1' WHERE id=?", (self.cli,))


class InspectRowTest(PatchesCase):
    def test_inspect_writes_bundle_info(self):
        self.add_cli()
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info")
        with mock.patch.object(upstream, "INSPECT", str(FIX / "fake-inspect.sh")):
            upstream.inspect_row(self.pat, "v1", FakeLog())
        pk = {r[0] for r in db.conn().execute("SELECT package FROM bundle_info WHERE upstream_id=? AND tag='v1'", (self.pat,))}
        self.assertEqual(pk, {YT, "com.reddit.frontpage"})

    def test_inspect_failure_writes_nothing(self):
        self.add_cli()
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info")
        log = FakeLog()
        with mock.patch.object(upstream, "INSPECT", str(FIX / "fake-inspect-fail.sh")):
            upstream.inspect_row(self.pat, "v1", log)
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM bundle_info").fetchone()[0], 0)
        self.assertTrue(any(l.startswith("[!] inspect failed for") for l in log.lines))

    def test_inspect_rejects_symlinked_output(self):
        """A runner output that is a symlink (here to a secret) is not read."""
        self.add_cli()
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info")
        secret = self.data / "secret.txt"
        secret.write_text((FIX / "list-patches.txt").read_text())
        script = self.data / "inspect-link.sh"
        script.write_text(f'ln -s {secret} "$3/list-patches.txt"\ncp {FIX}/options.json "$3/"\n')
        log = FakeLog()
        with mock.patch.object(upstream, "INSPECT", str(script)):
            upstream.inspect_row(self.pat, "v1", log)
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM bundle_info").fetchone()[0], 0)
        self.assertTrue(any(l.startswith("[!] inspect failed for") for l in log.lines), log.lines)

    def test_inspect_runs_isolated(self):
        """The inspector runs bundle code as RELUME_RUNNER_UID with DATA paths in the runner view.
        T_PY runs as root, so setpriv can switch uid here."""
        self.add_cli()
        with db.tx() as c:
            c.execute("DELETE FROM bundle_info")
        os.chmod(self.data, 0o755)  # TemporaryDirectory is 0700; in compose the runner gets its own mounts
        view = self.data / "view"
        view.symlink_to(self.data)
        secret = self.data / "secret"
        secret.write_text("s")
        secret.chmod(0o600)
        script = self.data / "inspect-id.sh"
        script.write_text(f'echo "UID $(id -u)"; echo "HOME $HOME"; echo "CLI $1"; echo "FILE $4"\n'
                          f'grep CapEff /proc/self/status; cat {secret} 2>/dev/null && echo LEAK || echo DENIED\n'
                          f'cp {FIX}/list-patches.txt {FIX}/options.json "$3/"\n')
        log = FakeLog()
        with mock.patch.object(upstream, "INSPECT", str(script)), \
                mock.patch.dict(os.environ, {"RELUME_RUNNER_UID": "10001", "RELUME_RUNNER_DATA": str(view)}):
            upstream.inspect_row(self.pat, "v1", log)
        self.assertEqual(log.runs[0][:2], ["setpriv", "--reuid=10001"])
        for want in ("UID 10001", f"CLI {view}/upstream/{self.cli}/v1.18.1/morphe-desktop-1.18.1-all.jar",
                     f"FILE {view}/upstream/{self.pat}/v1/patches-1.mpp", "CapEff:\t0000000000000000", "DENIED"):
            self.assertIn(want, log.lines)
        home = [l for l in log.lines if l.startswith("HOME ")]
        self.assertTrue(home and home[0] != "HOME " + os.environ.get("HOME", ""), home)
        pk = {r[0] for r in db.conn().execute("SELECT package FROM bundle_info WHERE upstream_id=? AND tag='v1'", (self.pat,))}
        self.assertEqual(pk, {YT, "com.reddit.frontpage"})


class ReinspectTest(PatchesCase):
    def test_pull_reinspects_missing_bundle_with_same_tag(self):
        d = self.data / "upstream" / str(self.pat) / "v1.47.0"
        d.mkdir(parents=True)
        (d / "patches-1.47.0.mpp").write_bytes(b"hello")
        self.add_cli()
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag='v1.47.0' WHERE id=?", (self.pat,))
        self.http({f"{API}/repos/MorpheApp/morphe-patches/releases/latest": (200, fix("release-stable.json"), {})})
        with mock.patch.object(upstream, "INSPECT", str(FIX / "fake-inspect.sh")):
            upstream.pull(self.pat, FakeLog())
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM bundle_info WHERE tag='v1.47.0'").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
