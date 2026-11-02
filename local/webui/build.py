"""Build job service (spec Part 3): job.json, runner child process, artifacts, retention, routes."""
import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import NamedTuple

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
RUNNER_TIMEOUT = 3600  # s, one isolated runner run: downloads, merges and patches of every variant
# failures a build retries only when its inputs change (decision 9, same rule as the patch guard)
RETRY_ON_INPUT_CHANGE = ("patch_items_failed", "upstream_missing", "merge_failed", "apk_not_listed")
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


def _record_fail(job, item_id, code, detail):
    with db.tx() as c:
        c.execute("""INSERT INTO build_jobs(job_id, item_id, fail_code, fail_detail) VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET fail_code = excluded.fail_code, fail_detail = excluded.fail_detail""",
                  (job.id, item_id, code, detail))


def _fail(job, item_id, code, detail):
    _record_fail(job, item_id, code, detail)
    events.emit("build.failed", {"item_id": item_id, "job_id": job.id, "fail_code": code, "fail_detail": detail})
    raise jobs.JobFailed(f"{code}: {detail}")


def _read_result(work):
    """result.json of a runner that exited 0 (spec build §3.2: ≥1 variant, every file non-empty inside work).
    Raises ValueError naming the cause."""
    try:
        with jobs.open_runner_file(work, "result.json") as f:
            r = json.load(f)
    except OSError as e:
        raise ValueError(f"result.json: cannot open: {e.strerror or e}") from None
    except ValueError as e:
        raise ValueError(f"result.json: invalid JSON: {e}") from None
    variants = r.get("variants") if isinstance(r, dict) else None
    if not isinstance(variants, list) or not variants:
        raise ValueError(f"result.json: expected a non-empty variants list, got {variants!r}")
    for v in variants:
        name = v.get("file") if isinstance(v, dict) else None
        if not isinstance(name, str):
            raise ValueError(f"result.json: variant without a file name: {v!r}")
        try:
            with jobs.open_runner_file(work, name) as f:
                size = os.fstat(f.fileno()).st_size
        except OSError as e:
            raise ValueError(f"variant file {name}: cannot open: {e.strerror or e}") from None
        if size == 0:
            raise ValueError(f"variant file {name}: empty")
    return r


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


def _register(job, item_id, inp, work, result, failed_patches, sha, cache_names):
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
        c.execute("UPDATE build_jobs SET app_version = ?, patches_version = ?, cli_version = ?, failed_patches = ?, "
                  "inputs_sha = ?, apk_cache = ? WHERE job_id = ?",
                  (result["app_version"], inp["upstream_tag"], inp["cli_tag"], json.dumps(failed_patches) if failed_patches else None,
                   sha, json.dumps(cache_names), job.id))
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


_CACHE_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")   # replaced by "_" in a cache name part


def _cache_key(s):
    return _CACHE_UNSAFE.sub("_", s)


def cache_name(package, version, download):
    """The one APK cache naming rule (pipe-08): name under DATA/apk-cache of the input of one download spec. Same format
    the runner used before, so files cached then still hit: <package>/<version>-<type>-<arch|any>-<dpi|any>.<apk|apkm>."""
    kind = download.get("type") or "apk"
    ext = "apkm" if kind == "bundle_extract" else "apk"
    return (f"{package}/{_cache_key(version)}-{_cache_key(kind)}-{_cache_key(download.get('arch') or 'any')}"
            f"-{_cache_key(download.get('dpi') or 'any')}.{ext}")


def _apk_cache(inp, auto):
    """[(download spec, cache name)] of the specs whose input the cache may serve and store: a concrete version without
    nearest-version fallback, not Google Play (the server downloads it), and for APKMirror only with an expected
    versionCode (manual list or remembered variant), because the name has no versionCode; the runner checks the code."""
    want = inp["version"]["value"]
    if want is None or inp["version"]["near"]:
        return []
    out = []
    for v in inp["recipe"]["variants"]:
        d = v["download"]
        if d["kind"] == "googleplay" or any(d == x for x, _ in out):
            continue
        if d["kind"] == "apkmirror" and not d.get("version_codes") and not any(a["download"] == d and a["pick"] for a in auto):
            continue
        out.append((d, cache_name(inp["package"], want, d)))
    return out


def _stale_cache(package, keep):
    """Entries of DATA/apk-cache/<package>/ whose cache name is not in keep."""
    d = db.DATA / "apk-cache" / package
    return [f for f in sorted(d.iterdir()) if f"{package}/{f.name}" not in keep] if d.is_dir() else []


def _remove(p):
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink(missing_ok=True)


def _prune_cache(job, package, keep, log):
    """After a published build: remove the app's cache entries it did not record (the runner no longer deletes older
    versions). A failure is logged, not fatal: the build is already published and clean-up removes the leftovers."""
    for p in _stale_cache(package, keep):
        try:
            _remove(p)
        except OSError as e:
            log.line(f"[!] apk-cache prune failed: {p}: {e}; artifact of job {job.id} kept, clean-up removes the file")


class _Rejected(NamedTuple):
    """An attempt that produced no publishable build. variant: the variant was refused (the patcher refused it or
    applied nothing), the next one may fit; else the runner failed for another reason (code, detail)."""
    variant: bool
    code: str      # fail code of a non-variant failure, "" for a variant refusal
    detail: str
    used: list     # [(auto entry, ::apkm_variant:: marker)] of this attempt


def _write_job_json(awork, job, inp, googleplay, auto, cache):
    """job.json (+ options.json) of one attempt; paths as the isolated runner sees them. cache = _apk_cache entries."""
    rp = jobs.runner_path
    picks = [{"download": a["download"], "exclude_codes": a["exclude"], **(a["pick"] or {})} for a in auto]
    (awork / "job.json").write_text(json.dumps(dict(inp, job_id=job.id, work_dir=rp(awork), cli_jar=rp(inp["cli_jar"]),
                                                    patches_files=[rp(f) for f in inp["patches_files"]],
                                                    googleplay=googleplay, apkmirror_picks=picks,
                                                    apk_cache=[{"download": d, "file": rp(db.DATA / "apk-cache" / n)}
                                                               for d, n in cache]), indent=2))
    if inp["options_file_json"] is not None:
        (awork / "options.json").write_text(json.dumps(inp["options_file_json"]))


def _guard_morphe(job, item_id, awork, result, sha):
    """Patch guard of a morphe build (design guard §1): (failed patch names, verdict or None). A verdict is stored with
    the failed names and the fingerprint, so the build is retried only once its inputs change."""
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
    return failed, verdict


def _attempt(job, item_id, inp, awork, googleplay, auto, sha, log):
    """One isolated runner run in awork (design variant §1). None when the build was published or cancelled, else
    _Rejected. Timeout, guard rules R0/R2 and a rejected artifact end the job here through _fail."""
    want = inp["version"]["value"]
    cache = _apk_cache(inp, auto)
    _write_job_json(awork, job, inp, googleplay, auto, cache)
    m = _Markers(job.id, inp["package"])
    try:
        rc = jobs.run_isolated(log, ["bash", RUNNER, jobs.runner_path(awork / "job.json")], jobs.runner_path(awork),
                               env_keys=jobs.SOLVER_ENV, timeout=RUNNER_TIMEOUT, on_line=m.feed)
    except jobs.JobTimeout:
        _fail(job, item_id, "timeout", f"runner exceeded {RUNNER_TIMEOUT} s")
    if job.cancel_event.is_set():
        return None
    used = [(next(a for a in auto if a["download"] == u["download"]), u) for u in m.apkm]
    for a, u in used:
        if a["pick"] and a["pick"]["version_code"] != u["version_code"]:   # the remembered page serves another build
            _apkm_forget(inp["package"], want, a["abis"])
            a["pick"] = None
    if rc != 0:   # the runner prints ::fail:: only when it exits 1: rc 0 carries no fail code
        if used and m.fail_code == "patch_failed":   # the patcher refusing an automatic variant: next one
            return _Rejected(True, "", m.fail_detail or "the patcher failed", used)
        if m.fail_code:
            return _Rejected(False, m.fail_code, m.fail_detail or m.last_err or "", used)
        return _Rejected(False, "internal", m.last_err or f"runner exited {rc} without result.json", used)
    try:
        result = _read_result(awork)
    except ValueError as e:
        return _Rejected(False, "internal", str(e), used)
    for device, vc in sorted({(g["device"], g["version_code"]) for g in googleplay if g["version_code"] is not None}):
        playversion.remember(inp["package"], want, device, vc)  # the runner verified name and code
    if want is None:  # latest, or auto with no compatible list: the runner refreshed the APKMirror list
        sha = apps.inputs_sha(apps.item_row(item_id))
    # lspatch/npatch embed one module: no per-patch result (design guard §7)
    failed, verdict = _guard_morphe(job, item_id, awork, result, sha) if inp["engine"] == "morphe" else ([], None)
    if verdict is None:
        for a, u in used:
            _apkm_remember(inp["package"], u["version_name"], a["abis"], u["version_code"], u["variant_url"], u["bundle"])
        try:
            _register(job, item_id, inp, awork, result, failed, sha, [n for _, n in cache])
        except OSError as e:
            shutil.rmtree(db.DATA / "artifacts" / str(item_id) / str(job.id), ignore_errors=True)
            _fail(job, item_id, "internal", f"artifact rejected: {e}")
        _prune_cache(job, inp["package"], {n for _, n in cache}, log)
        return None
    if not (used and verdict[0] == "nothing_applied"):   # R0, R2: not a verdict on the variant
        _fail(job, item_id, "patch_items_failed", verdict[1])
    return _Rejected(True, "", verdict[1], used)


def _runner_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o770)  # the isolated runner uid shares the server's gid


def _open_row(job, item_id, sha):
    """The build_jobs row with the input fingerprint, before anything can fail (decision 9)."""
    with db.tx() as c:
        c.execute("INSERT INTO build_jobs(job_id, item_id, inputs_sha) VALUES (?, ?, ?)", (job.id, item_id, sha))


def _start_row(job, item_id, inp):
    """Tags of this build; upstream_missing when a downloaded patch or CLI file is gone."""
    with db.tx() as c:
        c.execute("UPDATE build_jobs SET patches_version = ?, cli_version = ? WHERE job_id = ?",
                  (inp["upstream_tag"], inp["cli_tag"], job.id))
    missing = [p for p in [*inp["patches_files"], inp["cli_jar"]] if not Path(p).is_file()]
    if missing:
        _fail(job, item_id, "upstream_missing", f"missing file {Path(missing[0]).name}")


def _refingerprint_if_latest(job, item_id, inp):
    """Latest mode: fingerprint the version the runner found (it refreshes the APKMirror list during the run)."""
    if inp["version"]["value"] is None:
        with db.tx() as c:
            c.execute("UPDATE build_jobs SET inputs_sha = ? WHERE job_id = ?", (apps.inputs_sha(apps.item_row(item_id)), job.id))


def _fail_exhausted(job, item_id, inp, tried, vname, last):
    """End of a variant loop with no build: patch_items_failed waits for an input change (needs_build), whichever
    way the last variant failed."""
    _refingerprint_if_latest(job, item_id, inp)
    _fail(job, item_id, "patch_items_failed",
          f"Tried APKMirror variants {', '.join(map(str, tried))} of {vname}: the patches applied "
          f"to none of them. Last: {last.rstrip('.')}. Set the app's APKMirror versionCode to pick one.")


def handler(job, log):
    item_id = job.target["item_id"]
    work = db.DATA / "work" / str(job.id)
    try:
        r = apps.item_row(item_id)
        sha = apps.inputs_sha(r)
        _open_row(job, item_id, sha)
        if not r["enabled"]:   # disabled while queued (decision 4): no runner, no notification
            detail = f"app {item_id} ({r['package']}) was disabled after this build was queued"
            _record_fail(job, item_id, "app_disabled", detail)
            raise jobs.JobFailed(f"app_disabled: {detail}")
        try:
            inp = apps.job_input(item_id)
        except catalog.UpstreamMissing as e:
            _fail(job, item_id, "upstream_missing", str(e))
        for name in apps.stale_overrides(item_id):
            log.line(f"[!] stale override dropped: {name}")
        _runner_dir(work)
        _start_row(job, item_id, inp)
        googleplay = _googleplay(job, item_id, inp, work, log)
        auto, want, tried, vname, last = _apkm_auto(inp), inp["version"]["value"], [], None, ""
        # one runner run per APKMirror variant attempt, each in a fresh dir: the runner uid owns its files, the finally
        # below removes them all
        for attempt in range(1, MAX_VARIANT_ATTEMPTS + 1):
            awork = work / str(attempt)
            _runner_dir(awork)
            if attempt > 1:
                log.line(f"[*] APKMirror variant attempt {attempt}/{MAX_VARIANT_ATTEMPTS}, excluding versionCodes "
                         + ", ".join(map(str, tried)))
            out = _attempt(job, item_id, inp, awork, googleplay, auto, sha, log)
            if out is None:
                return
            if not out.variant:
                if not tried:
                    if out.code in RETRY_ON_INPUT_CHANGE:
                        _refingerprint_if_latest(job, item_id, inp)
                    _fail(job, item_id, out.code, out.detail)
                last = f"{last.rstrip('.')}. Then {out.code}: {out.detail}"   # end as the exhausted loop, not a retried code
                break
            last = out.detail
            for a, u in out.used:   # wrong variant: exclude its code, try the next one
                a["exclude"].append(u["version_code"])
                tried.append(u["version_code"])
                vname = u["version_name"]
                if a["pick"]:   # the remembered variant no longer applies (e.g. a pinned older bundle)
                    _apkm_forget(inp["package"], want, a["abis"])
                    a["pick"] = None
            if not any(u["remaining"] is None or u["remaining"] > 1 for _, u in out.used):
                break
        _fail_exhausted(job, item_id, inp, tried, vname, last)
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
    enabled app uses, cache files the newest success build of an enabled app did not record, artifacts/<id>
    dirs without an item row and upstream/<id> dirs without an upstream row. Active-job items are skipped."""
    c = db.conn()
    items = c.execute("SELECT id, package, enabled FROM item").fetchall()
    status = item_status([r["id"] for r in items])
    busy = {r["package"] for r in items if status[r["id"]]["active_job"]}
    off = [r["id"] for r in items if not r["enabled"] and not status[r["id"]]["active_job"]
           and (db.DATA / "artifacts" / str(r["id"])).exists()]
    keep = {}  # package -> cache names recorded by the newest success build of a Build-on item
    for r in items:
        if r["enabled"]:
            ls = status[r["id"]]["last_success"]
            keep[r["package"]] = set(ls["apk_cache"]) if ls else set()
    paths = []
    cache = db.DATA / "apk-cache"
    for d in sorted(cache.iterdir()) if cache.is_dir() else []:
        if d.name in busy or not d.is_dir():
            continue
        paths += [d] if d.name not in keep else _stale_cache(d.name, keep[d.name])
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
            _remove(p)
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
  b.fail_code, b.app_version, b.patches_version, b.cli_version, b.inputs_sha, b.apk_cache
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
                                 "inputs_sha": r["inputs_sha"], "apk_cache": json.loads(r["apk_cache"] or "[]"),
                                 "variants": []}
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
    """Apps part §4.3: never built, last build failed (a code in RETRY_ON_INPUT_CHANGE: and the inputs changed since),
    artifacts cleaned up, or the build inputs changed. st = item_status entry, r = apps.ROW_SQL row of the item."""
    ls, lb = st["last_success"], st["last_build"]
    if lb is not None and lb["status"] == "failed":  # decision 9: these codes retry only once the inputs change
        if lb["fail_code"] not in RETRY_ON_INPUT_CHANGE:
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
