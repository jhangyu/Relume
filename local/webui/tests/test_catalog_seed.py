"""Seed import / export tests (spec catalog §8, §10, ci §4.4)."""
import json
import re
import unittest

from webui import catalog, db
from webui.tests import helpers


class SeedImportTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()

    def test_import_once_with_daily_schedule(self):
        self.assertTrue(catalog.seed_import())
        self.assertFalse(catalog.seed_import())
        rows = db.conn().execute("SELECT name, kind, target, spec, options, enabled FROM schedules").fetchall()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["name"], r["kind"], r["enabled"]), ("Daily upstream", "upstream", 1))
        self.assertEqual(json.loads(r["target"]), {"all": True})
        self.assertEqual(json.loads(r["spec"]), {"every": "day", "at": "09:00"})
        self.assertIs(json.loads(r["options"])["rebuild"], True)
        self.assertIsNotNone(db.conn().execute("SELECT next_run_at FROM schedules").fetchone()[0])
        seeded = json.loads(catalog.SEED_PATH.read_text())
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM item").fetchone()[0], len(seeded["items"]))

    def test_export_round_trip_bytes(self):
        catalog.seed_import()
        self.assertEqual(catalog.seed_text(), catalog.SEED_PATH.read_text())


REPORT = catalog.REPO_ROOT / "docs" / "logs" / "2026-10-19-seed-report.md"


class CommittedSeedTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.d = json.loads(catalog.SEED_PATH.read_text())

    def test_counts(self):
        self.assertEqual(len(self.d["sources"]), 20)
        self.assertEqual(len(self.d["items"]), 64)
        xp = [i for i in self.d["items"] if i["source_id"] in ("revenge-lspatch", "nexalloy-lspatch", "nexalloy-npatch")]
        self.assertEqual(len(xp), 8)
        fb = [i for i in xp if i["package"] == "com.facebook.katana"][0]
        self.assertEqual((fb["version_mode"], fb["version_pin"]), ("pinned", "580.0.0.51.74"))

    def test_abis(self):
        kept_all = set(int(x) for x in re.findall(r"^- item (\d+)", REPORT.read_text().split("## Kept all ABIs")[1].split("\n## ")[0], re.M))
        for it in self.d["items"]:
            abis = {v["abi"] for v in it["variants"]}
            if it["id"] not in kept_all:
                self.assertLessEqual(abis, {"universal", "arm64-v8a"}, it["id"])

    def test_round_trip(self):
        catalog.seed_import()
        self.assertEqual(catalog.seed_text(), catalog.SEED_PATH.read_text())


if __name__ == "__main__":
    unittest.main()
