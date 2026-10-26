"""Build job service (spec Part 3): job.json, runner child process, artifacts, retention, routes."""
import json
import os
import re
import shutil
import threading
from pathlib import Path

from . import apps, catalog, db, events, jobs, server, settings

ARTIFACT_RETENTION_DEFAULT = 1  # successful builds kept per item
RUNNER = str(db.REPO_ROOT / "src" / "build" / "build-item.sh")
FAIL_CODES = ("upstream_missing", "apk_not_listed", "download_failed",
              "merge_failed", "patch_failed", "timeout", "internal")
SOLVER_ENV = ("FLARESOLVERR_URL", "CFBYPASS_URL", "APKM_SOLVER_URL")  # passed to the isolated runner
FAILED_PATCH_PREFIX = "ERROR: FAILED: "  # patcher CLI line for a patch that did not apply
_steps = {}
_steps_lock = threading.Lock()


class _Markers:
    """on_line callback: ::step::, ::version::, ::apkm_versions::, ::fail::, last [-] line (spec build §3.2)."""

    def __init__(self, job_id, package):
        self.job_id, self.package = job_id, package
        self.fail_code = self.fail_detail = self.last_err = self.versions = None
        self.failed_patches = []

    def feed(self, text):
        if text.startswith("::step::"):
            parts = text[8:].split("::")
            st = {"step": parts[0], "step_index": None, "step_count": None}
            if len(parts) > 1 and re.fullmatch(r"\d+/\d+", parts[1]):
                i, n = parts[1].split("/")
                st.update(step_index=int(i), step_count=int(n))
            with _steps_lock:
                _steps[self.job_id] = st
        elif text.startswith("::version::"):
            with db.tx() as c:
                c.execute("UPDATE build_jobs SET app_version = ? WHERE job_id = ?", (text[11:].strip(), self.job_id))
        elif text.startswith("::apkm_versions::"):
            versions = [v for v in text[17:].strip().split(",") if v]
            if versions:
                self.versions = versions
                catalog.set_apkmirror_versions(self.package, versions)
        elif text.startswith("::fail::"):
            code, _, detail = text[8:].partition("::")
            self.fail_code = code if code in FAIL_CODES else "internal"
            self.fail_detail = detail.strip()
        elif text.startswith(FAILED_PATCH_PREFIX):  # the CLI runs with --continue-on-error: the build still succeeds
            name = text[len(FAILED_PATCH_PREFIX):].strip()
            if name not in self.failed_patches:  # one line per variant
                self.failed_patches.append(name)
        elif text.startswith("[-]"):
            self.last_err = text.strip()


def _fail(job, item_id, code, detail):
    with db.tx() as c:
        c.execute("""INSERT INTO build_jobs(job_id, item_id, fail_code, fail_detail) VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET fail_code = excluded.fail_code, fail_detail = excluded.fail_detail""",
                  (job.id, item_id, code, detail))
    events.emit("build.failed", {"item_id": item_id, "job_id": job.id, "fail_code": code, "fail_detail": detail})
    raise jobs.JobFailed(f"{code}: {detail}")


def _read_result(work):
    """result.json when valid (spec build §3.2: present, ≥1 variant, every file non-empty inside work)."""
    try:
        with jobs.open_runner_file(work, "result.json") as f:
            r = json.load(f)
        variants = r["variants"]
        if not variants:
            return None
        for v in variants:
            with jobs.open_runner_file(work, v["file"]) as f:
                if os.fstat(f.fileno()).st_size == 0:
                    return None
        return r
    except (OSError, ValueError, KeyError, TypeError):
        return None


def artifact_name(package, variant, app_version, upstream_tag):
    return f"{package}-{variant}-{server.safe_remote(app_version)}-{server.safe_remote(upstream_tag)}.apk"


def _register(job, item_id, inp, work, result, failed_patches):
    dest = db.DATA / "artifacts" / str(item_id) / str(job.id)
    dest.mkdir(parents=True, exist_ok=True)
    now, ids = db.utcnow(), []
    with db.tx() as c:
        for v in result["variants"]:
            target = dest / artifact_name(inp["package"], v["name"], result["app_version"], inp["upstream_tag"])
            # copy from the fd validated now, not the path validated earlier
            with jobs.open_runner_file(work, v["file"]) as src, open(target, "xb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            ids.append(c.execute("""INSERT INTO artifacts(job_id, item_id, variant, abi, package, version_name, version_code,
                patches_version, signer_sha256, path, size, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                 (job.id, item_id, v["name"], v["abi"], v.get("package"), v.get("version_name"),
                                  v.get("version_code"), inp["upstream_tag"], v.get("signer_sha256"), str(target),
                                  target.stat().st_size, v.get("sha256"), now)).lastrowid)
        c.execute("UPDATE build_jobs SET app_version = ?, patches_version = ?, cli_version = ?, failed_patches = ? WHERE job_id = ?",
                  (result["app_version"], inp["upstream_tag"], inp["cli_tag"], json.dumps(failed_patches) if failed_patches else None, job.id))
    run_retention(item_id)
    events.emit("build.succeeded", {"item_id": item_id, "job_id": job.id, "app_version": result["app_version"],
                                    "patches_version": inp["upstream_tag"], "artifacts": ids})


def handler(job, log):
    item_id = job.target["item_id"]
    work = db.DATA / "work" / str(job.id)
    try:
        try:
            inp = apps.job_input(item_id)
        except catalog.UpstreamMissing as e:
            _fail(job, item_id, "upstream_missing", str(e))
        sha = apps.inputs_sha(item_id)
        for name in apps.stale_overrides(item_id):
            log.line(f"[!] stale override dropped: {name}")
        work.mkdir(parents=True, exist_ok=True)
        work.chmod(0o770)  # the isolated runner uid shares the server's gid
        rp = jobs.runner_path
        rwork = rp(work)
        (work / "job.json").write_text(json.dumps(dict(inp, job_id=job.id, work_dir=rwork, cli_jar=rp(inp["cli_jar"]),
                                                       patches_files=[rp(f) for f in inp["patches_files"]]), indent=2))
        if inp["options_file_json"] is not None:
            (work / "options.json").write_text(json.dumps(inp["options_file_json"]))
        with db.tx() as c:
            c.execute("INSERT INTO build_jobs(job_id, item_id, patches_version, cli_version, inputs_sha) VALUES (?, ?, ?, ?, ?)",
                      (job.id, item_id, inp["upstream_tag"], inp["cli_tag"], sha))
        missing = [p for p in [*inp["patches_files"], inp["cli_jar"]] if not Path(p).is_file()]
        if missing:
            _fail(job, item_id, "upstream_missing", f"missing file {Path(missing[0]).name}")
        m = _Markers(job.id, inp["package"])
        try:
            rc = jobs.run_isolated(log, ["bash", RUNNER, rp(work / "job.json")], rwork, env_keys=SOLVER_ENV,
                                   env={"RELUME_APK_CACHE": rp(db.DATA / "apk-cache")}, timeout=3600, on_line=m.feed)
        except jobs.JobFailed as e:
            if str(e) == "timeout":
                _fail(job, item_id, "timeout", "runner exceeded 3600 s")
            raise
        if job.cancel_event.is_set():
            return
        result = _read_result(work) if rc == 0 else None
        if result is None:
            if m.fail_code:
                _fail(job, item_id, m.fail_code, m.fail_detail or "")
            _fail(job, item_id, "internal", m.last_err or f"runner exited {rc} without result.json")
        try:
            _register(job, item_id, inp, work, result, m.failed_patches)
        except OSError as e:
            shutil.rmtree(db.DATA / "artifacts" / str(item_id) / str(job.id), ignore_errors=True)
            _fail(job, item_id, "internal", f"artifact rejected: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with _steps_lock:
            _steps.pop(job.id, None)


def _artifact_obj(r):
    d = dict(r)
    path = d.pop("path")
    d.update(file_name=Path(path).name, download_url=f"/api/artifacts/{r['id']}/download")
    return d


def detail(job_id):
    b = db.conn().execute("SELECT * FROM build_jobs WHERE job_id = ?", (job_id,)).fetchone()
    arts = [_artifact_obj(r) for r in db.conn().execute("SELECT * FROM artifacts WHERE job_id = ? ORDER BY id", (job_id,))]
    with _steps_lock:
        st = dict(_steps.get(job_id, {}))
    b = dict(b) if b else {}
    b["failed_patches"] = json.loads(b.get("failed_patches") or "[]")
    return {**b, "step": st.get("step"), "step_index": st.get("step_index"),
            "step_count": st.get("step_count"), "artifacts": arts}


def register():
    jobs.register("build", handler, detail)


def start():
    """No job can run yet: stale work dirs go (spec build §6)."""
    w = db.DATA / "work"
    if w.is_dir():
        for d in w.iterdir():
            shutil.rmtree(d, ignore_errors=True)
    cache = db.DATA / "apk-cache"  # the isolated runner (same gid) writes the APK cache
    if cache.stat().st_uid == os.getuid():  # a host-owned dir (CI) keeps its mode
        cache.chmod(0o770)


def run_retention(item_id=None):
    """Keep the artifacts of the newest N jobs per item; the newest is never deleted (spec build §6)."""
    n = max(1, int(settings.get("artifact_retention", ARTIFACT_RETENTION_DEFAULT)))
    c = db.conn()
    items = [item_id] if item_id is not None else [r[0] for r in c.execute("SELECT DISTINCT item_id FROM artifacts")]
    for iid in items:
        job_ids = [r[0] for r in c.execute("SELECT DISTINCT job_id FROM artifacts WHERE item_id = ? ORDER BY job_id DESC", (iid,))]
        for old in job_ids[n:]:
            for r in c.execute("SELECT path FROM artifacts WHERE job_id = ?", (old,)).fetchall():
                Path(r["path"]).unlink(missing_ok=True)
            with db.tx() as t:
                t.execute("DELETE FROM artifacts WHERE job_id = ?", (old,))
            shutil.rmtree(db.DATA / "artifacts" / str(iid) / str(old), ignore_errors=True)


def _files(path):
    return [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else [path]


def _orphan_dirs(c, name, table):
    """<DATA>/<name>/<id> dirs whose row in table is gone (migration 010 deletes rows, SQL cannot delete files)."""
    base = db.DATA / name
    ids = {r[0] for r in c.execute(f"SELECT id FROM {table}")}  # table is one of the fixed names below
    return [d for d in sorted(base.iterdir()) if d.is_dir() and not (d.name.isdigit() and int(d.name) in ids)] \
        if base.is_dir() else []


def _cleanup_plan():
    """What clean-up removes (spec cleanup, apps part §6): artifacts of disabled apps, apk-cache of packages no
    enabled app uses, cache files of other versions than the newest success build of an enabled app, artifacts/<id>
    dirs without an item row and upstream/<id> dirs without an upstream row. Active-job items are skipped."""
    c = db.conn()
    items = c.execute("SELECT id, package, enabled FROM item").fetchall()
    status = item_status([r["id"] for r in items])
    busy = {r["package"] for r in items if status[r["id"]]["active_job"]}
    off = [r["id"] for r in items if not r["enabled"] and not status[r["id"]]["active_job"]
           and (db.DATA / "artifacts" / str(r["id"])).exists()]
    keep = {}  # package -> cache version keys of Build-on items
    for r in items:
        if r["enabled"]:
            ls = status[r["id"]]["last_success"]
            keep.setdefault(r["package"], set())
            if ls and ls["app_version"]:
                keep[r["package"]].add(re.sub(r"[^A-Za-z0-9._-]", "_", ls["app_version"]))
    paths = []
    cache = db.DATA / "apk-cache"
    for d in sorted(cache.iterdir()) if cache.is_dir() else []:
        if d.name in busy or not d.is_dir():
            continue
        if d.name not in keep:
            paths.append(d)
        else:  # file name = <vkey>-<type>-<arch>-<dpi>.<ext>; a version that is a hyphen-prefix of another is kept (safe side)
            paths += [f for f in sorted(d.iterdir()) if not any(f.name.startswith(k + "-") for k in keep[d.name])]
    return off, paths, _orphan_dirs(c, "artifacts", "item") + _orphan_dirs(c, "upstream", "upstream")


def cleanup(apply):
    off, paths, orphans = _cleanup_plan()
    dirs = [db.DATA / "artifacts" / str(i) for i in off]
    out = {"bytes": sum(f.stat().st_size for p in dirs + paths + orphans for f in _files(p)),
           "artifact_items": len(off), "cache_files": sum(len(_files(p)) for p in paths), "orphan_dirs": len(orphans)}
    if apply:
        with db.tx() as t:
            t.executemany("DELETE FROM artifacts WHERE item_id = ?", [(i,) for i in off])
        for p in dirs + paths + orphans:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    return out


def _newest_success_job(item_id):
    r = db.conn().execute("""SELECT a.job_id FROM artifacts a JOIN jobs j ON j.id = a.job_id
        WHERE a.item_id = ? AND j.status = 'success' ORDER BY a.job_id DESC LIMIT 1""", (item_id,)).fetchone()
    return r[0] if r else None


def latest_artifact(item_id, abi):
    """Artifacts row of the newest success build with this ABI, else universal, else None."""
    jid = _newest_success_job(item_id)
    if jid is None:
        return None
    for want in (abi, "universal"):
        a = db.conn().execute("SELECT * FROM artifacts WHERE job_id = ? AND abi = ? ORDER BY id LIMIT 1", (jid, want)).fetchone()
        if a:
            return dict(a)
    return None


STATUS_SQL = """SELECT j.id, j.status, j.reason, j.finished_at, json_extract(j.target, '$.item_id') AS item_id,
  b.fail_code, b.app_version, b.patches_version, b.cli_version
  FROM jobs j LEFT JOIN build_jobs b ON b.job_id = j.id
  WHERE j.kind = 'build' AND json_extract(j.target, '$.item_id') IN ({q})"""


def item_status(item_ids):
    """{item_id: {last_build, last_success, active_job}} in two queries (spec build §3.4)."""
    out = {i: {"last_build": None, "last_success": None, "active_job": None} for i in item_ids}
    if not item_ids:
        return out
    sql = STATUS_SQL.format(q=",".join("?" * len(item_ids)))
    c = db.conn()
    for r in c.execute(sql + " AND j.status IN ('success', 'failed', 'cancelled') ORDER BY j.id", list(item_ids)):
        o = out[r["item_id"]]
        o["last_build"] = {"job_id": r["id"], "status": r["status"], "reason": r["reason"], "fail_code": r["fail_code"]}
        if r["status"] == "success":
            o["last_success"] = {"job_id": r["id"], "app_version": r["app_version"], "patches_version": r["patches_version"],
                                 "cli_version": r["cli_version"], "finished_at": r["finished_at"]}
    # queued rows newest-first, then running: the last assignment wins = running job, else the oldest queued
    for r in c.execute(sql + " AND j.status IN ('queued', 'running') ORDER BY j.status = 'running', j.id DESC", list(item_ids)):
        with _steps_lock:
            st = _steps.get(r["id"], {}) if r["status"] == "running" else {}
        out[r["item_id"]]["active_job"] = {"job_id": r["id"], "status": r["status"], "step": st.get("step"),
                                          "step_index": st.get("step_index"), "step_count": st.get("step_count")}
    return out


def needs_build(item_id):
    """Apps part §4.3: never built, last build failed, artifacts cleaned up, or the build inputs changed."""
    st = item_status([item_id])[item_id]
    ls, lb = st["last_success"], st["last_build"]
    if ls is None or lb["status"] == "failed":
        return True
    if not db.conn().execute("SELECT 1 FROM artifacts WHERE item_id = ?", (item_id,)).fetchone():
        return True  # cleaned up: the build is gone
    sha = db.conn().execute("SELECT inputs_sha FROM build_jobs WHERE job_id = ?", (ls["job_id"],)).fetchone()[0]
    return sha != apps.inputs_sha(item_id)  # NULL for builds before migration 010: rebuilt once


def get_cleanup(req):
    return 200, cleanup(apply=False)


def post_cleanup(req):
    return 200, cleanup(apply=True)


def list_artifacts(item_id):
    if not db.conn().execute("SELECT 1 FROM item WHERE id = ?", (item_id,)).fetchone():
        raise server.HttpError(404, f"app {item_id} not found")
    return [_artifact_obj(r) for r in db.conn().execute("SELECT * FROM artifacts WHERE item_id = ? ORDER BY id DESC", (item_id,))]


def download_artifact(req):
    r = db.conn().execute("SELECT path, sha256, signer_sha256, package, version_code FROM artifacts WHERE id = ?",
                          (req.params["id"],)).fetchone()
    if r is None or not Path(r["path"]).is_file():
        raise server.HttpError(404, "artifact not found")
    headers = {"X-Relume-Sha256": r["sha256"], "X-Relume-Signer-Sha256": r["signer_sha256"],
               "X-Relume-Package": r["package"], "X-Relume-Version-Code": r["version_code"]}
    missing = [k for k, v in headers.items() if v is None]
    if missing:
        raise server.HttpError(500, f"artifact {req.params['id']} has no value for {', '.join(missing)}: the app cannot verify the download")
    return server.FileResponse(r["path"], download_name=Path(r["path"]).name, etag=r["sha256"],
                               headers={k: str(v) for k, v in headers.items()})


ROUTES = [
    ("GET", "/api/cleanup", get_cleanup),
    ("POST", "/api/cleanup", post_cleanup),
    ("GET", "/api/artifacts/{id}/download", download_artifact),
]
