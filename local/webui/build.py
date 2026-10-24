"""Build job service (spec Part 3): job.json, runner child process, artifacts, retention, routes."""
import json
import os
import re
import shutil
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import catalog, db, events, jobs, server, settings

ARTIFACT_RETENTION_DEFAULT = 1  # successful builds kept per item
RUNNER = str(db.REPO_ROOT / "src" / "build" / "build-item.sh")
FAIL_CODES = ("upstream_missing", "version_unresolved", "apk_not_listed", "download_failed",
              "merge_failed", "patch_failed", "timeout", "internal")
CHECK_VERSIONS = str(db.REPO_ROOT / "src" / "build" / "check-versions.sh")
CHECK_TIMEOUT = 300  # one list-page request with solver retries
SOLVER_ENV = ("FLARESOLVERR_URL", "CFBYPASS_URL", "APKM_SOLVER_URL")  # passed to the isolated runner
_steps = {}
_steps_lock = threading.Lock()


class _Markers:
    """on_line callback: ::step::, ::version::, ::apkm_versions::, ::fail::, last [-] line (spec build §3.2)."""

    def __init__(self, job_id, package):
        self.job_id, self.package = job_id, package
        self.fail_code = self.fail_detail = self.last_err = self.versions = None

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


def artifact_name(source_id, key, variant, app_version, upstream_tag):
    return f"{source_id}-{key}-{variant}-{server.safe_remote(app_version)}-{server.safe_remote(upstream_tag)}.apk"


def _register(job, item_id, inp, work, result):
    dest = db.DATA / "artifacts" / str(item_id) / str(job.id)
    dest.mkdir(parents=True, exist_ok=True)
    now, ids = db.utcnow(), []
    with db.tx() as c:
        for v in result["variants"]:
            target = dest / artifact_name(inp["source_id"], inp["key"], v["name"], result["app_version"], inp["upstream_tag"])
            # copy from the fd validated now, not the path validated earlier
            with jobs.open_runner_file(work, v["file"]) as src, open(target, "xb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            ids.append(c.execute("""INSERT INTO artifacts(job_id, item_id, variant, abi, package, version_name, version_code,
                patches_version, signer_sha256, path, size, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                 (job.id, item_id, v["name"], v["abi"], v.get("package"), v.get("version_name"),
                                  v.get("version_code"), inp["upstream_tag"], v.get("signer_sha256"), str(target),
                                  target.stat().st_size, v.get("sha256"), now)).lastrowid)
        c.execute("UPDATE build_jobs SET app_version = ?, patches_version = ?, cli_version = ? WHERE job_id = ?",
                  (result["app_version"], inp["upstream_tag"], inp["cli_tag"], job.id))
    run_retention(item_id)
    events.emit("build.succeeded", {"item_id": item_id, "job_id": job.id, "app_version": result["app_version"],
                                    "patches_version": inp["upstream_tag"], "artifacts": ids})


def handler(job, log):
    item_id = job.target["item_id"]
    work = db.DATA / "work" / str(job.id)
    try:
        try:
            inp = catalog.job_input(item_id)
        except catalog.UpstreamMissing as e:
            _fail(job, item_id, "upstream_missing", str(e))
        for name in catalog.stale_overrides(item_id):
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
            c.execute("INSERT INTO build_jobs(job_id, item_id, patches_version, cli_version) VALUES (?, ?, ?, ?)",
                      (job.id, item_id, inp["upstream_tag"], inp["cli_tag"]))
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
            _register(job, item_id, inp, work, result)
        except OSError as e:
            shutil.rmtree(db.DATA / "artifacts" / str(item_id) / str(job.id), ignore_errors=True)
            _fail(job, item_id, "internal", f"artifact rejected: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with _steps_lock:
            _steps.pop(job.id, None)


def appcheck_handler(job, log):
    """appcheck job (spec per-app §3.2): refresh the APKMirror version list of one package, optionally rebuild."""
    package = job.target["package"]
    row = db.conn().execute("SELECT apkmirror_list_url FROM app WHERE package = ?", (package,)).fetchone()
    if row is None or not row["apkmirror_list_url"]:
        raise jobs.JobFailed(f"{package}: no APKMirror list URL")
    work = db.DATA / "work" / str(job.id)
    try:
        work.mkdir(parents=True, exist_ok=True)
        work.chmod(0o770)  # the isolated runner uid shares the server's gid
        m = _Markers(job.id, package)
        rc = jobs.run_isolated(log, ["bash", CHECK_VERSIONS, row["apkmirror_list_url"]], jobs.runner_path(work),
                               env_keys=SOLVER_ENV, timeout=CHECK_TIMEOUT, on_line=m.feed)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if job.cancel_event.is_set():
        return
    if rc != 0 or m.versions is None:
        raise jobs.JobFailed(f"{package}: version check failed: {m.last_err or f'runner exited {rc} without versions'}")
    log.line(f"[+] {package}: APKMirror newest {m.versions[0]} ({len(m.versions)} versions)")
    if (job.options or {}).get("rebuild"):
        ids = [r[0] for r in db.conn().execute(
            "SELECT id FROM item WHERE package = ? AND version_mode = 'latest' AND enabled = 1 AND auto_update = 1 ORDER BY id",
            (package,)) if needs_build(r[0])]
        for iid in ids:
            jobs.enqueue("build", {"item_id": iid}, "upstream")
        log.line(f"[+] rebuild enqueued for {len(ids)} item(s)")


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
    return {**(dict(b) if b else {}), "step": st.get("step"), "step_index": st.get("step_index"),
            "step_count": st.get("step_count"), "artifacts": arts}


def register():
    jobs.register("build", handler, detail)
    jobs.register("appcheck", appcheck_handler)


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


def forget_item(item_id):
    shutil.rmtree(db.DATA / "artifacts" / str(item_id), ignore_errors=True)


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
    st = item_status([item_id])[item_id]
    ls, lb = st["last_success"], st["last_build"]
    if ls is None or lb["status"] == "failed":
        return True
    r = db.conn().execute("""SELECT u.downloaded_tag AS p, c.downloaded_tag AS cl, i.version_mode, a.apkmirror_versions AS vs
        FROM item i JOIN source s ON s.id = i.source_id JOIN app a ON a.package = i.package
        JOIN upstream u ON u.id = s.upstream_id JOIN upstream c ON c.id = s.cli_upstream_id WHERE i.id = ?""", (item_id,)).fetchone()
    if r is None:
        return False
    if r["p"] != ls["patches_version"] or r["cl"] != ls["cli_version"]:
        return True
    newest = json.loads(r["vs"] or "[]")[:1]
    return r["version_mode"] == "latest" and bool(newest) and newest[0] != ls["app_version"]


def _item_exists(iid):
    if not db.conn().execute("SELECT 1 FROM item WHERE id = ?", (iid,)).fetchone():
        raise server.HttpError(404, "item not found")


def post_item_build(req):
    iid = req.params["id"]
    _item_exists(iid)
    return 200, {"job_id": jobs.enqueue("build", {"item_id": iid}, "manual")}


def post_builds(req):
    ids = (req.json or {}).get("item_ids")
    if not isinstance(ids, list) or not ids or not all(map(server.is_int, ids)):
        raise server.HttpError(400, "invalid item_ids")
    for i in ids:
        _item_exists(i)
    return 200, {"job_ids": [jobs.enqueue("build", {"item_id": i}, "manual") for i in ids]}


def list_item_artifacts(req):
    iid = req.params["id"]
    _item_exists(iid)
    return 200, {"artifacts": [_artifact_obj(r) for r in db.conn().execute("SELECT * FROM artifacts WHERE item_id = ? ORDER BY id DESC", (iid,))]}


def download_artifact(req):
    r = db.conn().execute("SELECT path FROM artifacts WHERE id = ?", (req.params["id"],)).fetchone()
    if r is None or not Path(r["path"]).is_file():
        raise server.HttpError(404, "artifact not found")
    return server.FileResponse(r["path"], download_name=Path(r["path"]).name)


def group_zip_files(group_id):
    files = []
    for iid in catalog.resolve_items({"group_ids": [group_id]}):
        jid = _newest_success_job(iid)
        if jid is not None:
            files += [(Path(r[0]), Path(r[0]).name) for r in db.conn().execute("SELECT path FROM artifacts WHERE job_id = ? ORDER BY id", (jid,))]
    return files


def _slug(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.").lower() or "group"


def download_group(req):
    gid = req.params["id"]
    if gid == 0:
        name = "ungrouped"
    else:
        r = db.conn().execute("SELECT name FROM grp WHERE id = ?", (gid,)).fetchone()
        if r is None:
            raise server.HttpError(404, "group not found")
        name = r[0]
    files = group_zip_files(gid)
    if not files:
        raise server.HttpError(404, "no artifacts")

    def write(f):
        # ZIP_STORED: APKs are already compressed; each file is opened right before it is written (spec build §3.4)
        with zipfile.ZipFile(f, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for path, arc in files:
                try:
                    src = open(path, "rb")
                except FileNotFoundError:
                    continue
                with src, z.open(zipfile.ZipInfo(arc, time.localtime()[:6]), "w", force_zip64=True) as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)

    return server.StreamResponse(write, f"{_slug(name)}-{datetime.now().strftime('%Y%m%d')}.zip")


ROUTES = [
    ("POST", "/api/items/{id}/build", post_item_build),
    ("POST", "/api/builds", post_builds),
    ("GET", "/api/items/{id}/artifacts", list_item_artifacts),
    ("GET", "/api/artifacts/{id}/download", download_artifact),
    ("GET", "/api/groups/{id}/download", download_group),
]
