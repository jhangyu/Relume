"""Seed import / export tests (spec catalog §8, §10, ci §4.4)."""
import json
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


if __name__ == "__main__":
    unittest.main()
