"""Build job service (spec Part 3): job.json, runner child process, artifacts, retention, routes."""
import json
import os
import re
import shutil
import threading
from pathlib import Path

from . import apps, catalog, db, events, jobs, playversion, server, settings

ARTIFACT_RETENTION_DEFAULT = 1  # successful builds kept per item
RUNNER = str(db.REPO_ROOT / "src" / "build" / "build-item.sh")
FAIL_CODES = ("upstream_missing", "apk_not_listed", "download_failed",
              "merge_failed", "patch_failed", "patch_items_failed", "timeout", "internal")
APKEEP = "apkeep"
APKEEP_TIMEOUT = 1800  # one Google Play login + download per distinct googleplay spec
APKEEP_DEVICE = {"arm64-v8a": "px_9a", "armeabi-v7a": "sm_a13_5g", "x86_64": "google_kiwi_x86_64"}  # single-ABI profiles
APKEEP_MULTI_DEVICE = "nothing_p1"  # arm64-v8a + armeabi-v7a, SDK 34: universal or several ABIs (mi_a1 is SDK 29, too old for some apps)
FAILED_NAMES_SHOWN = 5  # failed patch names listed in a guard verdict before "and N more"
ERROR_TEXT_MAX = 300  # chars of the patcher's error kept in a guard verdict
# APKMirror variants tried per build (design variant §3): each attempt is one download.php hit plus a full download,
# merge and patch, far below the rate limit (about 17 hits in 11 s); the ordering finds the known cases on attempt 1
MAX_VARIANT_ATTEMPTS = 3
_steps = {}
_steps_lock = threading.Lock()


class _Markers:
    """on_line callback: ::step::, ::version::, ::apkm_versions::, ::apkm_variant::, ::fail::, last [-] line
    (spec build §3.2, design variant §1)."""

    def __init__(self, job_id, package):
        self.job_id, self.package = job_id, package
        self.fail_code = self.fail_detail = self.last_err = self.versions = None
        self.apkm = []   # {download, version_code, version_name, variant_url, bundle, remaining} per automatic download

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
        elif text.startswith("::apkm_variant::"):
            self.apkm.append(json.loads(text[16:]))
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


def _result_shape(r):
    """A morphe CLI -r result (appliedPatches: list of names, failedPatches: [{name, error}]) as
    {"applied": n, "failed": [(name, error)]}, None when not in that shape."""
    try:
        applied, failed = r["appliedPatches"], r["failedPatches"]
        out = [(p["name"], p["error"]) for p in failed]
    except (KeyError, TypeError):
        return None
    if not isinstance(applied, list) or not all(isinstance(n, str) and isinstance(e, str) for n, e in out):
        return None
    return {"applied": len(applied), "failed": out}


def _read_patch_result(work, variant):
    """release/<variant>.result.json written by the morphe CLI (-r, utils.sh patch_variant), None when missing or bad."""
    try:
        with jobs.open_runner_file(work, f"release/{variant}.result.json") as f:
            return _result_shape(json.load(f))
    except (OSError, ValueError):
        return None


def _error_text(error):
    """Root cause of a patcher exception: the last "Caused by:" line, else the first line; without the class name."""
    lines = error.splitlines() or [""]
    msg = next((s[len("Caused by: "):] for s in reversed(lines) if s.startswith("Caused by: ")), lines[0])
    return re.sub(r"^[\w.$]+(?:Exception|Error): ", "", msg)[:ERROR_TEXT_MAX].rstrip(".")


def patch_verdict(results, baseline, published):
    """Guard for a morphe build (design guard §1): results = {variant: _read_patch_result(...)}, baseline = failed
    names of the published build, published = its app version, None when nothing is published (no regression to
    judge: R2 is skipped). (rule, fail detail) with rule result_missing (R0), nothing_applied (R1) or regression (R2),
    or None when the build may publish."""
    tail = f"The previous build ({published}) stays published." if published else "No build was published."
    for name, r in results.items():
        if r is None:
            return "result_missing", f"patcher result file missing or unreadable for variant {name}. {tail}"
    for name, r in results.items():
        if r["applied"] == 0:
            first = f" First error: {_error_text(r['failed'][0][1])}." if r["failed"] else ""
            return "nothing_applied", f"0 of {len(r['failed'])} patches applied (variant {name}).{first} {tail}"
    if published is None:
        return None
    errors = {}
    for r in results.values():
        for n, e in r["failed"]:
            errors.setdefault(n, e)
    new = [n for n in errors if n not in baseline]
    if not new:
        return None
    names = ", ".join(new[:FAILED_NAMES_SHOWN]) + (f", ... and {len(new) - FAILED_NAMES_SHOWN} more" if len(new) > FAILED_NAMES_SHOWN else "")
    return "regression", (f"{len(new)} patches failed that work in the published build: {names}. "
                          f"First error: {_error_text(errors[new[0]])}. {tail}")


def artifact_name(package, variant, app_version, upstream_tag):
    return f"{package}-{variant}-{server.safe_remote(app_version)}-{server.safe_remote(upstream_tag)}.apk"


def _register(job, item_id, inp, work, result, failed_patches, sha):
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
        c.execute("UPDATE build_jobs SET app_version = ?, patches_version = ?, cli_version = ?, failed_patches = ?, inputs_sha = ? WHERE job_id = ?",
                  (result["app_version"], inp["upstream_tag"], inp["cli_tag"], json.dumps(failed_patches) if failed_patches else None, sha, job.id))
    run_retention(item_id)
    events.emit("build.succeeded", {"item_id": item_id, "job_id": job.id, "app_version": result["app_version"],
                                    "patches_version": inp["upstream_tag"], "artifacts": ids})


def _googleplay(job, item_id, inp, work, log):
    """apkeep download of each distinct googleplay download spec, run by the server outside run_isolated so the
    Google credentials never reach the isolated runner (only an ini file in the 0700 key dir names them, not argv
    or env). A concrete version is resolved to its versionCode per device first and downloaded as <pkg>@<versionCode>.
    Returns [{download, path, version_code, device}] for job.json: path = <pkg>.apk or the split dir <pkg>/."""
    variants = inp["recipe"]["variants"]
    specs = []
    for v in variants:
        if v["download"]["kind"] == "googleplay" and v["download"] not in specs:
            specs.append(v["download"])
    if not specs:
        return []
    gp = settings.google_play()
    if gp is None:
        _fail(job, item_id, "download_failed", "Google Play credentials not set in Settings")
    ini = db.KEY / f"apkeep-{job.id}.ini"
    settings.write_secret(ini, f"[google]\nemail = {gp['email']}\naas_token = {gp['aas_token']}\n")
    want = inp["version"]["value"]
    devices = []
    for d in specs:
        abis = sorted({v["abi"] for v in variants if v["download"] == d})  # universal among them -> multi-ABI
        devices.append(APKEEP_DEVICE[abis[0]] if len(abis) == 1 and abis[0] in APKEEP_DEVICE else APKEEP_MULTI_DEVICE)
    out = []
    try:
        codes = {}
        if want is not None:  # resolve every device before the first download: a lookup failure costs no download
            for device in dict.fromkeys(devices):
                play = playversion.PlayClient(apkeep=APKEEP, ini=ini, device=device, log=log)
                try:
                    codes[device] = playversion.resolve(inp["package"], want, log, play)  # resolve closes play
                except playversion.VersionCodeError as e:
                    _fail(job, item_id, "download_failed", str(e))
                log.line(f"[*] Google Play versionCode {codes[device]} for {inp['package']} {want} (device={device})")
        for n, (d, device) in enumerate(zip(specs, devices)):
            vc = codes.get(device)
            app = inp["package"] if vc is None else f"{inp['package']}@{vc}"
            dest = work / "googleplay" / str(n)
            dest.mkdir(parents=True)
            log.line(f"[*] Google Play download of {app} (device={device})")
            rc = log.run([APKEEP, "-a", app, "-d", "google-play", "-i", str(ini),
                          "-o", f"device={device},split_apk=true", str(dest)], cwd=str(dest), env=None,
                         timeout=APKEEP_TIMEOUT)
            got = [p for p in (dest / f"{inp['package']}.apk", dest / inp["package"]) if p.exists()]
            if rc != 0 or not got:
                _fail(job, item_id, "download_failed", f"apkeep exited {rc}: expected {inp['package']}.apk or "
                      f"{inp['package']}/ in the output, found {sorted(p.name for p in dest.iterdir())} "
                      f"(requested {app}" + (f" (versionName {want})" if vc is not None else "") + f", device={device}: "
                      "apkeep downloads nothing when Play does not offer the app or that version to that device profile)")
            out.append({"download": d, "path": jobs.runner_path(got[0]), "version_code": vc, "device": device})
    finally:
        ini.unlink(missing_ok=True)
    return out


def _apkm_remembered(package, version, abis):
    """The APKMirror variant that passed the guard for this version and ABI set, as an apkmirror_picks entry."""
    r = db.conn().execute("SELECT version_code, variant_url, bundle FROM apkm_variant WHERE package = ? AND "
                          "version_name = ? AND abis = ?", (package, version, abis)).fetchone()
    return None if r is None else {"version_code": r["version_code"], "variant_url": r["variant_url"], "bundle": bool(r["bundle"])}


def _apkm_remember(package, version, abis, version_code, variant_url, bundle):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO apkm_variant (package, version_name, abis, version_code, variant_url, bundle, "
                  "verified_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (package, version, abis, version_code, variant_url, int(bundle), db.utcnow()))


def _apkm_forget(package, version, abis):
    with db.tx() as c:
        c.execute("DELETE FROM apkm_variant WHERE package = ? AND version_name = ? AND abis = ?", (package, version, abis))


def _apkm_auto(inp):
    """Automatic APKMirror download specs (no manual version_codes): [{download, abis, pick, exclude}], pick = the
    remembered variant of a concrete version or None, exclude = versionCodes rejected by earlier attempts."""
    variants, want, out = inp["recipe"]["variants"], inp["version"]["value"], []
    for v in variants:
        d = v["download"]
        if d["kind"] == "apkmirror" and not d.get("version_codes") and all(a["download"] != d for a in out):
            abis = " ".join(sorted({x["abi"] for x in variants if x["download"] == d and x["abi"] != "universal"}))
            out.append({"download": d, "abis": abis, "exclude": [],
                        "pick": _apkm_remembered(inp["package"], want, abis) if want is not None else None})
    return out


def handler(job, log):
    item_id = job.target["item_id"]
    work = db.DATA / "work" / str(job.id)
    try:
        try:
            inp = apps.job_input(item_id)
        except catalog.UpstreamMissing as e:
            _fail(job, item_id, "upstream_missing", str(e))
        sha = apps.inputs_sha(apps.item_row(item_id))
        for name in apps.stale_overrides(item_id):
            log.line(f"[!] stale override dropped: {name}")
        work.mkdir(parents=True, exist_ok=True)
        work.chmod(0o770)  # the isolated runner uid shares the server's gid
        rp = jobs.runner_path
        with db.tx() as c:
            c.execute("INSERT INTO build_jobs(job_id, item_id, patches_version, cli_version, inputs_sha) VALUES (?, ?, ?, ?, ?)",
                      (job.id, item_id, inp["upstream_tag"], inp["cli_tag"], sha))
        missing = [p for p in [*inp["patches_files"], inp["cli_jar"]] if not Path(p).is_file()]
        if missing:
            _fail(job, item_id, "upstream_missing", f"missing file {Path(missing[0]).name}")
        googleplay = _googleplay(job, item_id, inp, work, log)
        auto, want, tried, vname = _apkm_auto(inp), inp["version"]["value"], [], None
        # one isolated runner run per APKMirror variant attempt (design variant §1), each in a fresh dir: the runner uid
        # owns its files, the finally below removes them all
        for attempt in range(1, MAX_VARIANT_ATTEMPTS + 1):
            awork = work / str(attempt)
            awork.mkdir(exist_ok=True)
            awork.chmod(0o770)
            if attempt > 1:
                log.line(f"[*] APKMirror variant attempt {attempt}/{MAX_VARIANT_ATTEMPTS}, excluding versionCodes "
                         + ", ".join(map(str, tried)))
            picks = [{"download": a["download"], "exclude_codes": a["exclude"], **(a["pick"] or {})} for a in auto]
            (awork / "job.json").write_text(json.dumps(dict(inp, job_id=job.id, work_dir=rp(awork), cli_jar=rp(inp["cli_jar"]),
                                                            patches_files=[rp(f) for f in inp["patches_files"]],
                                                            googleplay=googleplay, apkmirror_picks=picks), indent=2))
            if inp["options_file_json"] is not None:
                (awork / "options.json").write_text(json.dumps(inp["options_file_json"]))
            m = _Markers(job.id, inp["package"])
            try:
                rc = jobs.run_isolated(log, ["bash", RUNNER, rp(awork / "job.json")], rp(awork), env_keys=jobs.SOLVER_ENV,
                                       env={"RELUME_APK_CACHE": rp(db.DATA / "apk-cache")}, timeout=3600, on_line=m.feed)
            except jobs.JobTimeout:
                _fail(job, item_id, "timeout", "runner exceeded 3600 s")
            if job.cancel_event.is_set():
                return
            used = [(next(a for a in auto if a["download"] == u["download"]), u) for u in m.apkm]
            for a, u in used:
                if a["pick"] and a["pick"]["version_code"] != u["version_code"]:   # the remembered page serves another build
                    _apkm_forget(inp["package"], want, a["abis"])
                    a["pick"] = None
            result = _read_result(awork) if rc == 0 else None
            if result is None:
                if not (used and m.fail_code == "patch_failed"):   # the patcher refusing an automatic variant: next one
                    if tried:   # earlier variants were rejected: end as the exhausted loop, not as a code retried every run
                        rejected = (f"{rejected.rstrip('.')}. Then {m.fail_code or 'internal'}: "
                                    f"{m.fail_detail or m.last_err or f'runner exited {rc} without result.json'}")
                        break
                    if m.fail_code:
                        _fail(job, item_id, m.fail_code, m.fail_detail or "")
                    _fail(job, item_id, "internal", m.last_err or f"runner exited {rc} without result.json")
                rejected = m.fail_detail or "the patcher failed"
            else:
                for device, vc in sorted({(g["device"], g["version_code"]) for g in googleplay if g["version_code"] is not None}):
                    playversion.remember(inp["package"], want, device, vc)  # the runner verified name and code
                if want is None:  # latest, or auto with no compatible list: the runner refreshed the APKMirror list
                    sha = apps.inputs_sha(apps.item_row(item_id))
                failed, verdict = [], None
                if inp["engine"] == "morphe":  # lspatch/npatch embed one module: no per-patch result (design guard §7)
                    results = {v["name"]: _read_patch_result(awork, v["name"]) for v in result["variants"]}
                    failed = list(dict.fromkeys(n for r in results.values() if r for n, _ in r["failed"]))
                    ls = item_status([item_id])[item_id]["last_success"]
                    baseline = set(json.loads(db.conn().execute("SELECT failed_patches FROM build_jobs WHERE job_id = ?",
                                                                (ls["job_id"],)).fetchone()[0] or "[]")) if ls else set()
                    verdict = patch_verdict(results, baseline, ls["app_version"] if ls else None)
                    if verdict:
                        with db.tx() as c:
                            c.execute("UPDATE build_jobs SET failed_patches = ?, inputs_sha = ? WHERE job_id = ?",
                                      (json.dumps(failed) if failed else None, sha, job.id))
                if verdict is None:
                    for a, u in used:
                        _apkm_remember(inp["package"], u["version_name"], a["abis"], u["version_code"], u["variant_url"], u["bundle"])
                    try:
                        _register(job, item_id, inp, awork, result, failed, sha)
                    except OSError as e:
                        shutil.rmtree(db.DATA / "artifacts" / str(item_id) / str(job.id), ignore_errors=True)
                        _fail(job, item_id, "internal", f"artifact rejected: {e}")
                    return
                if not (used and verdict[0] == "nothing_applied"):   # R0, R2: not a verdict on the variant
                    _fail(job, item_id, "patch_items_failed", verdict[1])
                rejected = verdict[1]
            # wrong variant (R1 or the patcher refused it): exclude the used codes, try the next variant
            for a, u in used:
                a["exclude"].append(u["version_code"])
                tried.append(u["version_code"])
                vname = u["version_name"]
                if a["pick"]:   # the remembered variant no longer applies (e.g. a pinned older bundle)
                    _apkm_forget(inp["package"], want, a["abis"])
                    a["pick"] = None
            if not any(u["remaining"] is None or u["remaining"] > 1 for _, u in used):
                break
        # an exhausted loop waits for an input change (needs_build), whichever way the last variant failed
        if want is None:   # fingerprint the version the runner found (as the guard path does)
            with db.tx() as c:
                c.execute("UPDATE build_jobs SET inputs_sha = ? WHERE job_id = ?", (apps.inputs_sha(apps.item_row(item_id)), job.id))
        _fail(job, item_id, "patch_items_failed",
              f"Tried APKMirror variants {', '.join(map(str, tried))} of {vname}: the patches applied "
              f"to none of them. Last: {rejected.rstrip('.')}. Set the app's APKMirror versionCode to pick one.")
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
  b.fail_code, b.app_version, b.patches_version, b.cli_version, b.inputs_sha
  FROM jobs j LEFT JOIN build_jobs b ON b.job_id = j.id
  WHERE j.kind = 'build' AND json_extract(j.target, '$.item_id') IN ({q})"""


def item_status(item_ids):
    """{item_id: {last_build, last_success, active_job}} in three queries (spec build §3.4); last_success.variants =
    the variant names that still have an artifact, sorted."""
    out = {i: {"last_build": None, "last_success": None, "active_job": None} for i in item_ids}
    if not item_ids:
        return out
    sql = STATUS_SQL.format(q=",".join("?" * len(item_ids)))
    c = db.conn()
    for r in c.execute(sql + " AND j.status IN ('success', 'failed', 'cancelled') ORDER BY j.id", list(item_ids)):
        o = out[r["item_id"]]
        o["last_build"] = {"job_id": r["id"], "status": r["status"], "reason": r["reason"], "fail_code": r["fail_code"],
                           "inputs_sha": r["inputs_sha"]}
        if r["status"] == "success":
            o["last_success"] = {"job_id": r["id"], "app_version": r["app_version"], "patches_version": r["patches_version"],
                                 "cli_version": r["cli_version"], "finished_at": r["finished_at"],
                                 "inputs_sha": r["inputs_sha"], "variants": []}
    ls = {o["last_success"]["job_id"]: o["last_success"] for o in out.values() if o["last_success"]}
    if ls:
        for r in c.execute(f"SELECT DISTINCT job_id, variant FROM artifacts WHERE job_id IN ({','.join('?' * len(ls))}) "
                           "ORDER BY variant", list(ls)):
            ls[r["job_id"]]["variants"].append(r["variant"])
    # queued rows newest-first, then running: the last assignment wins = running job, else the oldest queued
    for r in c.execute(sql + " AND j.status IN ('queued', 'running') ORDER BY j.status = 'running', j.id DESC", list(item_ids)):
        with _steps_lock:
            st = _steps.get(r["id"], {}) if r["status"] == "running" else {}
        out[r["item_id"]]["active_job"] = {"job_id": r["id"], "status": r["status"], "step": st.get("step"),
                                          "step_index": st.get("step_index"), "step_count": st.get("step_count")}
    return out


def needs_build(st, r):
    """Apps part §4.3: never built, last build failed (a patch_items_failed verdict: and the inputs changed since),
    artifacts cleaned up, or the build inputs changed. st = item_status entry, r = apps.ROW_SQL row of the item."""
    ls, lb = st["last_success"], st["last_build"]
    if lb is not None and lb["status"] == "failed":  # a guard verdict is retried only once its inputs change
        if lb["fail_code"] != "patch_items_failed":
            return True
        return not apps.inputs_cover(lb["inputs_sha"], apps.inputs_sha(r))
    if ls is None:
        return True
    if not {v["name"] for v in json.loads(r["variants"])} <= set(ls["variants"]):
        return True  # cleaned up, or a variant's APK is gone
    return not apps.inputs_cover(ls["inputs_sha"], apps.inputs_sha(r))


def drop_variants(item_id, keep):
    """Delete the artifacts of variants no longer in the item's settings (an ABI was unticked)."""
    rows = db.conn().execute(f"SELECT id, path FROM artifacts WHERE item_id = ? AND variant NOT IN ({','.join('?' * len(keep))})",
                             (item_id, *keep)).fetchall()
    with db.tx() as t:
        t.executemany("DELETE FROM artifacts WHERE id = ?", [(r["id"],) for r in rows])
    for r in rows:
        Path(r["path"]).unlink(missing_ok=True)


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
