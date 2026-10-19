"""Build job service tests (spec build §3-§6, §8)."""
import json
import sqlite3
import unittest
from pathlib import Path

from webui import build, db
from webui.tests import helpers

FIX = Path(__file__).parent / "fixtures" / "build"
JOB_FIXTURE = json.loads((build.REPO_ROOT / "src" / "build" / "test-fixtures" / "job.json").read_text())
YT = "com.google.android.youtube"


def add_item(c, iid=1, package=YT, key="youtube", group_id=None):
    c.execute("INSERT OR IGNORE INTO upstream(id, kind, repo, channel, downloaded_tag) VALUES (1,'cli','c/c','stable','v1.0.0'),(2,'patches','p/p','stable','v1.2.3')")
    c.execute("INSERT OR IGNORE INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('morphe','Morphe',2,1)")
    c.execute("INSERT OR IGNORE INTO app(package) VALUES (?)", (package,))
    c.execute("INSERT INTO item(id, source_id, package, key, group_id, position, variants) VALUES (?, 'morphe', ?, ?, ?, 0, '[]')",
              (iid, package, key, group_id))


def add_job(c, item_id, status="running"):
    return c.execute("INSERT INTO jobs(kind, status, trigger, target, options, created_at) VALUES ('build', ?, 'manual', ?, '{}', '2026-10-19T00:00:00Z')",
                     (status, json.dumps({"item_id": item_id}, separators=(",", ":")))).lastrowid


class SchemaTest(helpers.DataTestCase):
    def test_restrict_and_cascade(self):
        with db.tx() as c:
            add_item(c)
            j1, j2 = add_job(c, 1, "success"), add_job(c, 1, "failed")
            c.execute("INSERT INTO build_jobs(job_id, item_id) VALUES (?, 1), (?, 1)", (j1, j2))
            c.execute("INSERT INTO artifacts(job_id, item_id, variant, abi, path, created_at) VALUES (?, 1, 'universal', 'universal', '/x', 'now')", (j1,))
        with self.assertRaises(sqlite3.IntegrityError):
            with db.tx() as c:
                c.execute("DELETE FROM jobs WHERE id = ?", (j1,))
        with db.tx() as c:
            c.execute("DELETE FROM jobs WHERE id = ?", (j2,))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM build_jobs").fetchone()[0], 1)
        with db.tx() as c:
            c.execute("DELETE FROM item WHERE id = 1")
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM build_jobs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
