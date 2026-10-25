"""Build routes and group ZIP tests (spec build §3.4, §8)."""
import io
import unittest
import zipfile
from datetime import datetime

from webui import build, db, server
from webui.tests import helpers
from webui.tests.test_build_service import BuildCase, add_item


class RoutesTest(BuildCase):
    def call(self, handler, json=None, method="POST", **params):
        return handler(helpers.fake_request(method, "/test", json=json, params=params))

    def status_of(self, handler, json=None, method="POST", **params):
        try:
            return self.call(handler, json=json, method=method, **params)[0]
        except server.HttpError as e:
            return e.status

    def success_build(self, item_id):
        job, _, err = self.run_handler(job=self.job(item_id))
        self.assertIsNone(err)
        with db.tx() as c:
            c.execute("UPDATE jobs SET status = 'success' WHERE id = ?", (job.id,))
        return job

    def test_enqueue_dedup_and_404(self):
        a = self.call(build.post_item_build, json={}, id=1)[1]["job_id"]
        b = self.call(build.post_item_build, json={}, id=1)[1]["job_id"]
        self.assertEqual(a, b)
        self.assertEqual(self.status_of(build.post_item_build, json={}, id=99), 404)
        with db.tx() as c:
            add_item(c, 2, "com.reddit.frontpage", "reddit")
        self.assertEqual(len(self.call(build.post_builds, json={"item_ids": [1, 2]})[1]["job_ids"]), 2)
        self.assertEqual(self.status_of(build.post_builds, json={"item_ids": []}), 400)

    def set_enabled(self, iid, v):
        with db.tx() as c:
            c.execute("UPDATE item SET enabled = ? WHERE id = ?", (v, iid))

    def test_build_off_is_409_and_enqueues_nothing(self):
        with db.tx() as c:
            add_item(c, 2, "com.reddit.frontpage", "reddit")
        self.set_enabled(2, 0)
        self.assertEqual(self.status_of(build.post_item_build, json={}, id=2), 409)
        with self.assertRaisesRegex(server.HttpError, "item 2"):
            self.call(build.post_builds, json={"item_ids": [1, 2]})
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_cleanup_rules_and_active_skip(self):
        with db.tx() as c:
            add_item(c, 2, "com.reddit.frontpage", "reddit")      # off, has artifacts
            add_item(c, 3, "com.off.only", "offonly")              # off, active job
            add_item(c, 4, "com.third", "third")                   # on, never built
        self.success_build(1)
        self.success_build(2)
        for i in (2, 3):
            self.set_enabled(i, 0)
        (self.data / "artifacts" / "3").mkdir()
        with db.tx() as c:
            c.execute("INSERT INTO jobs(kind, status, trigger, target, options, created_at) VALUES ('build', 'queued', 'manual', '{\"item_id\":3}', '{}', 'now')")
        cache = self.data / "apk-cache"
        files = {"com.google.android.youtube": ["20.1.0-apk-any-any.apk", "19.0.0-apk-any-any.apk"],
                 "com.reddit.frontpage": ["1.0-apk-any-any.apk"], "com.off.only": ["2.0-apk-any-any.apk"],
                 "com.unused": ["3.0-apk-any-any.apk"], "com.third": ["1.0-apk-any-any.apk"]}
        for pkg, names in files.items():
            (cache / pkg).mkdir(parents=True)
            for n in names:
                (cache / pkg / n).write_bytes(b"xx")
        (cache / "com.off.only" / "x").mkdir()  # a package with an active job is untouched
        got = self.call(build.get_cleanup, method="GET")[1]
        self.assertEqual((got["artifact_items"], got["cache_files"]), (1, 4))  # files: yt 19.0.0, reddit, third, unused
        self.assertGreater(got["bytes"], 0)
        self.assertTrue((self.data / "artifacts" / "2").exists())   # preview deletes nothing
        self.assertEqual(self.call(build.post_cleanup)[1], got)
        self.assertFalse((self.data / "artifacts" / "2").exists())
        self.assertTrue((self.data / "artifacts" / "1").exists())
        self.assertTrue((self.data / "artifacts" / "3").exists())
        left = lambda pkg: sorted(p.name for p in (cache / pkg).iterdir()) if (cache / pkg).exists() else None
        self.assertEqual(left("com.google.android.youtube"), ["20.1.0-apk-any-any.apk"])
        self.assertIsNone(left("com.reddit.frontpage"))
        self.assertIsNone(left("com.unused"))
        self.assertEqual(left("com.third"), [])
        self.assertEqual(left("com.off.only"), ["2.0-apk-any-any.apk", "x"])
        rows = lambda i: db.conn().execute("SELECT COUNT(*) FROM artifacts WHERE item_id = ?", (i,)).fetchone()[0]
        self.assertEqual((rows(1), rows(2)), (2, 0))

    def test_needs_build_without_artifacts(self):
        self.success_build(1)
        self.assertFalse(build.needs_build(1))
        with db.tx() as c:
            c.execute("DELETE FROM artifacts WHERE item_id = 1")
        self.assertTrue(build.needs_build(1))

    def test_artifacts_list_and_download(self):
        self.success_build(1)
        arts = self.call(build.list_item_artifacts, method="GET", id=1)[1]["artifacts"]
        self.assertEqual([a["variant"] for a in arts], ["arm64-v8a", "universal"])
        self.assertNotIn("path", arts[0])
        self.assertIsInstance(self.call(build.download_artifact, method="GET", id=arts[0]["id"]), server.FileResponse)
        p = db.conn().execute("SELECT path FROM artifacts WHERE id = ?", (arts[0]["id"],)).fetchone()[0]
        __import__("os").unlink(p)
        self.assertEqual(self.status_of(build.download_artifact, method="GET", id=arts[0]["id"]), 404)

    def test_group_zip(self):
        with db.tx() as c:
            c.execute("INSERT INTO grp(id, name, position) VALUES (5, 'My Group', 0)")
            c.execute("UPDATE item SET group_id = 5 WHERE id = 1")
            add_item(c, 2, "com.reddit.frontpage", "reddit", group_id=5)
        self.success_build(1)
        resp = self.call(build.download_group, method="GET", id=5)
        self.assertIsInstance(resp, server.StreamResponse)
        self.assertEqual(resp.download_name, f"my-group-{datetime.now().strftime('%Y%m%d')}.zip")
        buf = io.BytesIO()
        resp.write_fn(buf)
        z = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
        names = sorted(z.namelist())
        self.assertEqual(names, ["morphe-youtube-arm64-v8a-20.1.0-v1.2.3.apk", "morphe-youtube-universal-20.1.0-v1.2.3.apk"])
        sizes = {Path_.name: Path_.stat().st_size for Path_ in (self.data / "artifacts" / "1").rglob("*.apk")}
        self.assertEqual({i.filename: i.file_size for i in z.infolist()}, sizes)
        with db.tx() as c:
            c.execute("INSERT INTO grp(id, name, position) VALUES (6, 'Empty', 1)")
        self.assertEqual(self.status_of(build.download_group, method="GET", id=6), 404)


if __name__ == "__main__":
    unittest.main()
