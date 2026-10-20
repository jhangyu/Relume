"""Build job service tests (spec build §3-§6, §8)."""
import json
import os
import sqlite3
import subprocess
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from webui import build, db, events, jobs, settings
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



class StreamLog:
    """jobs log stand-in that streams lines to on_line while the child runs."""

    def __init__(self):
        self.lines, self.env, self.argv = [], None, None

    def line(self, text):
        self.lines.append(text)

    def run(self, argv, cwd, env, timeout=None, on_line=None):
        self.argv, self.env = argv, env
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for text in p.stdout:
            text = text.rstrip("\n")
            self.lines.append(text)
            if on_line:
                on_line(text)
        p.stdout.close()
        return p.wait()


class FakeCatalog:
    class UpstreamMissing(Exception):
        pass

    def __init__(self, up):
        self.up, self.missing, self.key, self.versions = up, False, "youtube", []

    def job_input(self, item_id):
        if self.missing:
            raise self.UpstreamMissing("source upstream 2 not downloaded")
        j = {k: v for k, v in JOB_FIXTURE.items() if k not in ("job_id", "work_dir")}
        j.update(item_id=item_id, key=self.key, patches_files=[str(self.up / "patches-1.0.0.mpp")],
                 cli_jar=str(self.up / "morphe-desktop-1.0.0-all.jar"))
        return j

    def stale_overrides(self, item_id):
        return ["Gone"]

    def set_apkmirror_versions(self, package, versions):
        self.versions.append((package, versions))

    def resolve_items(self, target):
        return [r[0] for r in db.conn().execute("SELECT id FROM item WHERE ifnull(group_id, 0) = ? ORDER BY id", (target["group_ids"][0],))]


class BuildCase(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.data / "up"
        self.up.mkdir()
        (self.up / "patches-1.0.0.mpp").write_bytes(b"p")
        (self.up / "morphe-desktop-1.0.0-all.jar").write_bytes(b"c")
        self.cat = FakeCatalog(self.up)
        for attr, val in (("CATALOG", self.cat), ("RUNNER", str(FIX / "runner-ok.sh"))):
            p = mock.patch.object(build, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.events = []
        p = mock.patch.object(events, "emit", side_effect=lambda t, pl: self.events.append((t, pl)))
        p.start()
        self.addCleanup(p.stop)
        with db.tx() as c:
            add_item(c)
        self.addCleanup(build._steps.clear)

    def job(self, item_id=1, status="running"):
        with db.tx() as c:
            jid = add_job(c, item_id, status)
        return SimpleNamespace(id=jid, target={"item_id": item_id}, options={}, cancel_event=threading.Event())

    def run_handler(self, runner=None, job=None):
        job = job or self.job()
        if runner:
            build.RUNNER = str(FIX / runner)
        log = StreamLog()
        try:
            build.handler(job, log)
            return job, log, None
        except jobs.JobFailed as e:
            return job, log, e

    def bj(self, job):
        return db.conn().execute("SELECT * FROM build_jobs WHERE job_id = ?", (job.id,)).fetchone()


class HandlerFailTest(BuildCase):
    def test_job_json_matches_runner_fixture(self):
        seen = {}
        real = StreamLog.run

        def spy(log, argv, cwd, env, timeout=None, on_line=None):
            seen.update(json.loads(Path(argv[2]).read_text()))
            seen["_options"] = json.loads((Path(cwd) / "options.json").read_text())
            return real(log, argv, cwd, env, timeout, on_line)

        with mock.patch.object(StreamLog, "run", spy):
            self.run_handler("runner-noresult.sh")
        self.assertEqual(set(seen) - {"_options"}, set(JOB_FIXTURE))
        self.assertEqual(set(seen["recipe"]["variants"][0]), set(JOB_FIXTURE["recipe"]["variants"][0]))
        self.assertEqual(seen["_options"], JOB_FIXTURE["options_file_json"])

    def test_upstream_missing_from_catalog(self):
        self.cat.missing = True
        job, log, err = self.run_handler()
        self.assertEqual(self.bj(job)["fail_code"], "upstream_missing")
        self.assertTrue(str(err).startswith("upstream_missing: "))
        self.assertIsNone(log.argv)

    def test_missing_file_is_upstream_missing(self):
        (self.up / "morphe-desktop-1.0.0-all.jar").unlink()
        job, log, err = self.run_handler()
        self.assertEqual(self.bj(job)["fail_code"], "upstream_missing")
        self.assertIsNone(log.argv)

    def test_each_fail_code(self):
        for code in ("version_unresolved", "apk_not_listed", "download_failed", "merge_failed", "patch_failed", "internal"):
            self.cat.key = code
            job, log, err = self.run_handler("runner-fail.sh")
            self.assertEqual((self.bj(job)["fail_code"], str(err)), (code, f"{code}: detail of {code}"))
        self.assertEqual(self.events[-1], ("build.failed", {"item_id": 1, "job_id": job.id, "fail_code": "internal", "fail_detail": "detail of internal"}))

    def test_no_marker_and_no_result(self):
        job, log, err = self.run_handler("runner-nomarker.sh")
        self.assertEqual((self.bj(job)["fail_code"], self.bj(job)["fail_detail"]), ("internal", "[-] last error line"))
        job, log, err = self.run_handler("runner-noresult.sh")
        self.assertEqual(self.bj(job)["fail_code"], "internal")
        self.assertFalse((self.data / "work" / str(job.id)).exists())

    def test_env_allowlist(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret", "FLARESOLVERR_URL": "http://127.0.0.1:47191"}):
            job, log, err = self.run_handler("runner-env.sh")
        keys = set(next(l for l in log.lines if l.startswith("ENVKEYS ")).split()[1:])
        allowed = {"PATH", "HOME", "TZ", "LANG", "FLARESOLVERR_URL", "CFBYPASS_URL", "APKM_SOLVER_URL", "RV_APK_CACHE",
                   "PWD", "SHLVL", "_", "OLDPWD"}  # the last four are set by bash itself
        self.assertLessEqual(keys, allowed)
        self.assertNotIn("GITHUB_TOKEN", keys)
        self.assertIn("FLARESOLVERR_URL", keys)
        self.assertNotIn("GITHUB_TOKEN", log.env)

    def test_markers(self):
        job = self.job()
        with db.tx() as c:
            c.execute("INSERT INTO build_jobs(job_id, item_id) VALUES (?, 1)", (job.id,))
        m = build._Markers(job.id, YT)
        m.feed("::version::20.1.0")
        self.assertEqual(self.bj(job)["app_version"], "20.1.0")
        m.feed("::apkm_versions::20.2.0,20.1.0")
        self.assertEqual(self.cat.versions, [(YT, ["20.2.0", "20.1.0"])])
        m.feed("::step::patch::2/3")
        self.assertEqual(build._steps[job.id], {"step": "patch", "step_index": 2, "step_count": 3})
        m.feed("::fail::bogus::x")
        self.assertEqual(m.fail_code, "internal")

    def test_cancel_emits_nothing(self):
        job = self.job()
        job.cancel_event.set()
        _, _, err = self.run_handler(job=job)
        self.assertIsNone(err)
        self.assertEqual(self.events, [])
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)

    def test_stale_override_logged(self):
        job, log, err = self.run_handler("runner-noresult.sh")
        self.assertIn("[!] stale override dropped: Gone", log.lines)

    def test_start_removes_stale_work_dirs(self):
        (self.data / "work" / "77").mkdir(parents=True)
        build.start()
        self.assertEqual(list((self.data / "work").iterdir()), [])

    def test_start_keeps_foreign_owned_apk_cache_mode(self):
        """CI case: the workflow made apk-cache as the host user. start() must not retighten it."""
        if os.geteuid() != 0:
            self.skipTest("chown needs root (T_PY runs as root)")
        cache = self.data / "apk-cache"
        cache.mkdir(exist_ok=True)
        cache.chmod(0o755)
        os.chown(cache, 1001, 1001)
        build.start()
        self.assertEqual(cache.stat().st_mode & 0o777, 0o755)


class IsolationTest(BuildCase):
    """Spec L779: patch code runs as RV_RUNNER_UID, sees DATA through RV_RUNNER_DATA (compose: /data), keeps no caps.
    T_PY runs as root, so setpriv can switch uid here."""

    def test_runner_uid_paths_and_secret(self):
        os.chmod(self.data, 0o755)  # TemporaryDirectory is 0700; in compose the runner gets its own mounts instead
        view = self.data / "view"
        view.symlink_to(self.data)  # a second path to DATA, as the compose runner mounts are
        secret = self.data / "secret"
        secret.write_text("s")
        secret.chmod(0o600)
        script = self.data / "runner-id.sh"
        script.write_text(f'echo "UID $(id -u)"; echo "HOME $HOME"; echo "WORK $(jq -r .work_dir "$1")"\n'
                          f'echo "CLI $(jq -r .cli_jar "$1")"; echo "CACHE $RV_APK_CACHE"; grep CapEff /proc/self/status\n'
                          f'cat {secret} 2>/dev/null && echo LEAK || echo DENIED; touch out && echo WROTE\n')
        build.RUNNER = str(script)
        with mock.patch.dict(os.environ, {"RV_RUNNER_UID": "10001", "RV_RUNNER_DATA": str(view)}):
            job, log, err = self.run_handler()
        work = f"{view}/work/{job.id}"
        self.assertEqual(log.argv[:2], ["setpriv", "--reuid=10001"])
        for want in ("UID 10001", f"HOME {work}", f"WORK {work}", f"CLI {view}/up/morphe-desktop-1.0.0-all.jar",
                     f"CACHE {view}/apk-cache", "CapEff:\t0000000000000000", "DENIED", "WROTE"):
            self.assertIn(want, log.lines)
        self.assertFalse((self.data / "work" / str(job.id)).exists())

    def test_unset_runs_unchanged(self):
        job, log, err = self.run_handler("runner-noresult.sh")
        self.assertEqual(log.argv[0], "bash")
        self.assertTrue(log.argv[2].startswith(str(self.data)))


class HandlerOkTest(BuildCase):
    def test_success_registers_artifacts(self):
        job, log, err = self.run_handler()
        self.assertIsNone(err)
        rows = db.conn().execute("SELECT * FROM artifacts WHERE job_id = ? ORDER BY id", (job.id,)).fetchall()
        self.assertEqual([Path(r["path"]).name for r in rows],
                         ["morphe-youtube-universal-20.1.0-v1.2.3.apk", "morphe-youtube-arm64-v8a-20.1.0-v1.2.3.apk"])
        self.assertTrue(all(Path(r["path"]).is_file() for r in rows))
        self.assertEqual([(r["size"], r["sha256"], r["signer_sha256"]) for r in rows], [(1, "aa", "bb"), (3, "cc", "bb")])
        b = self.bj(job)
        self.assertEqual((b["app_version"], b["patches_version"], b["cli_version"], b["fail_code"]), ("20.1.0", "v1.2.3", "v1.0.0", None))
        self.assertEqual(self.events[-1], ("build.succeeded", {"item_id": 1, "job_id": job.id, "app_version": "20.1.0",
                                                               "patches_version": "v1.2.3", "artifacts": [r["id"] for r in rows]}))
        self.assertFalse((self.data / "work" / str(job.id)).exists())
        self.assertEqual(len(build.detail(job.id)["artifacts"]), 2)


class RetentionTest(BuildCase):
    def build_n(self, n, item_id=1):
        jobs_ = []
        for _ in range(n):
            job, _, err = self.run_handler(job=self.job(item_id))
            self.assertIsNone(err)
            with db.tx() as c:
                c.execute("UPDATE jobs SET status = 'success' WHERE id = ?", (job.id,))
            jobs_.append(job.id)
        return jobs_

    def job_ids_with_files(self, item_id=1):
        return sorted({r[0] for r in db.conn().execute("SELECT job_id FROM artifacts WHERE item_id = ?", (item_id,))})

    def test_keep_n(self):
        with mock.patch.object(settings, "get", side_effect=lambda k, d=None: 2 if k == "artifact_retention" else d):
            ids = self.build_n(4)
        self.assertEqual(self.job_ids_with_files(), ids[2:])
        self.assertEqual(sorted(int(p.name) for p in (self.data / "artifacts" / "1").iterdir()), ids[2:])

    def test_zero_keeps_newest(self):
        with mock.patch.object(settings, "get", side_effect=lambda k, d=None: 0 if k == "artifact_retention" else d):
            ids = self.build_n(2)
        self.assertEqual(self.job_ids_with_files(), ids[-1:])

    def test_run_retention_all_items(self):
        with db.tx() as c:
            add_item(c, 2, "com.reddit.frontpage", "reddit")
        a, b = self.build_n(2, 1), self.build_n(2, 2)
        with mock.patch.object(settings, "get", side_effect=lambda k, d=None: 1 if k == "artifact_retention" else d):
            build.run_retention()
        self.assertEqual((self.job_ids_with_files(1), self.job_ids_with_files(2)), (a[-1:], b[-1:]))

    def test_latest_artifact_and_forget(self):
        self.assertIsNone(build.latest_artifact(1, "arm64-v8a"))
        self.build_n(1)
        self.assertEqual(build.latest_artifact(1, "arm64-v8a")["abi"], "arm64-v8a")
        self.assertEqual(build.latest_artifact(1, "x86")["abi"], "universal")
        build.forget_item(1)
        self.assertFalse((self.data / "artifacts" / "1").exists())


class StatusTest(BuildCase):
    def finished(self, status, patches="v1.2.3", cli="v1.0.0"):
        with db.tx() as c:
            jid = add_job(c, 1, status)
            c.execute("UPDATE jobs SET finished_at = '2026-10-19T01:00:00Z' WHERE id = ?", (jid,))
            c.execute("INSERT INTO build_jobs(job_id, item_id, fail_code, app_version, patches_version, cli_version) VALUES (?, 1, ?, '20.1.0', ?, ?)",
                      (jid, "patch_failed" if status == "failed" else None, patches, cli))
        return jid

    def test_never_built(self):
        self.assertEqual(build.item_status([1]), {1: {"last_build": None, "last_success": None, "active_job": None}})
        self.assertTrue(build.needs_build(1))

    def test_success_and_tags(self):
        jid = self.finished("success")
        st = build.item_status([1])[1]
        self.assertEqual(st["last_build"], {"job_id": jid, "status": "success", "reason": None, "fail_code": None})
        self.assertEqual(st["last_success"]["patches_version"], "v1.2.3")
        self.assertFalse(build.needs_build(1))
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag = 'v1.3.0' WHERE id = 2")
        self.assertTrue(build.needs_build(1))
        with db.tx() as c:
            c.execute("UPDATE upstream SET downloaded_tag = 'v1.2.3' WHERE id = 2")
            c.execute("UPDATE upstream SET downloaded_tag = 'v1.1.0' WHERE id = 1")
        self.assertTrue(build.needs_build(1))

    def test_failed_newest(self):
        self.finished("success")
        self.finished("failed")
        self.assertEqual(build.item_status([1])[1]["last_build"]["fail_code"], "patch_failed")
        self.assertTrue(build.needs_build(1))

    def test_active_job_steps(self):
        queued = self.job(status="queued")
        st = build.item_status([1])[1]["active_job"]
        self.assertEqual(st, {"job_id": queued.id, "status": "queued", "step": None, "step_index": None, "step_count": None})
        running = self.job(status="running")
        build._Markers(running.id, YT).feed("::step::patch::2/3")
        st = build.item_status([1])[1]["active_job"]
        self.assertEqual(st, {"job_id": running.id, "status": "running", "step": "patch", "step_index": 2, "step_count": 3})


if __name__ == "__main__":
    unittest.main()
