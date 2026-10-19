"""Catalog store tests (spec catalog §3, §6, §10)."""
import sqlite3
import unittest

from webui import db
from webui.tests import helpers


class SchemaTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()

    def test_tables(self):
        names = {r[0] for r in db.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"upstream", "source", "app", "grp", "item", "bundle_info",
                              "dependency_app", "item_dependency"}, names)

    def test_upstream_unique_with_null_tag(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('patches','a/b','stable')")
        with self.assertRaises(sqlite3.IntegrityError):
            with db.tx() as c:
                c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('patches','a/b','stable')")

    def test_group_delete_and_item_cascade(self):
        with db.tx() as c:
            c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','c/c','stable'),(2,'patches','p/p','stable'),(3,'dependency','m/m','stable')")
            c.execute("INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('s','S',2,1)")
            c.execute("INSERT INTO app(package) VALUES ('com.a.b')")
            c.execute("INSERT INTO grp(id, name, position) VALUES (1,'G',0)")
            c.execute("INSERT INTO item(id, source_id, package, key, group_id, position, variants) VALUES (1,'s','com.a.b','b',1,0,'[]')")
            c.execute("INSERT INTO dependency_app(id, upstream_id, package, display_name) VALUES (1,3,'x.y','X')")
            c.execute("INSERT INTO item_dependency(item_id, dependency_app_id) VALUES (1,1)")
            c.execute("DELETE FROM grp WHERE id=1")
        self.assertIsNone(db.conn().execute("SELECT group_id FROM item WHERE id=1").fetchone()[0])
        with db.tx() as c:
            c.execute("DELETE FROM item WHERE id=1")
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM item_dependency").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
