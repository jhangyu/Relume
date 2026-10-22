"""Followed projects: store, routes, release verification, signer pinning (spec 2026-10-23-follow-project-design §4-§6)."""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from webui import catalog, db, events, jobs, server, upstream

ABI_TOKENS = ("arm64-v8a", "armeabi-v7a", "x86_64", "aarch64", "armeabi", "arm64", "armv7", "x86")
APKSIGNER = ["apksigner"]
APKEDITOR = ["java", "-jar", os.environ.get("RELUME_APKEDITOR", "/opt/APKEditor.jar")]
_ABI_SPLIT = re.compile("(" + "|".join(map(re.escape, ABI_TOKENS)) + ")")
_NUM = re.compile(r"\d+(?:\.\d+)*")
_NUM_PATTERN = r"\d+(?:\.\d+)*"
_SIGNER = re.compile(r"^Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)$")


# ---- pure functions (§4.1) ----

def _regex_part(name):
    out = []
    for i, seg in enumerate(_ABI_SPLIT.split(name)):
        if i % 2:  # an ABI token stays literal
            out.append(re.escape(seg))
            continue
        pos = 0
        for m in _NUM.finditer(seg):
            out.append(re.escape(seg[pos:m.start()]))
            run = m.group()
            loose = "." in run or m.start() == 0 or not seg[m.start() - 1].isalpha()
            out.append(_NUM_PATTERN if loose else re.escape(run))
            pos = m.end()
        out.append(re.escape(seg[pos:]))
    return "".join(out)


def derive_regex(names):
    return "^(?:" + "|".join(sorted({_regex_part(n) for n in names})) + ")$"


def apk_abi(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError):
        raise ValueError(f"not an APK: {Path(path).name}")
    dirs = {n.split("/")[1] for n in names if n.startswith("lib/") and n.count("/") >= 2}
    return next(iter(dirs)) if len(dirs) == 1 and dirs <= set(catalog.ABIS) else "universal"


def parse_apk_info(text):
    """Port of src/build/utils.sh apk_info."""
    lines = text.replace("\r", "").replace('"', "").splitlines()

    def first(rx):
        for line in lines:
            if re.search(rx, line):
                m = re.search(r"[=:]", line)
                return line[m.end():].strip() if m else ""
        return None

    pkg = first(r"(?i)^\s*package")
    if not pkg or not server.PACKAGE.match(pkg):
        return None
    code = first(r"(?i)version[-_ ]?code")
    return {"package": pkg, "version_code": int(code) if code and code.isdigit() else None,
            "version_name": first(r"(?i)version[-_ ]?name") or None}


def parse_signer(text):
    """None unless exactly one digest line: a certificate DN may carry a newline and a fake digest line."""
    found = [m.group(1).lower() for m in map(_SIGNER.match, (line.strip() for line in text.splitlines())) if m]
    return found[0] if len(found) == 1 else None


def pick_apk(facts, abis):
    facts = sorted(facts, key=lambda f: f["file"])
    for abi in [*abis, "universal"]:
        for f in facts:
            if f["abi"] == abi:
                return f
    return None


def label(row):
    return row["display_name"] or row["repo"].split("/", 1)[-1]


# ---- store functions consumed by ota/devices (§4.2) ----

def item_projects(item_id):
    return [r[0] for r in db.conn().execute(
        "SELECT project_id FROM item_dependency WHERE item_id = ? ORDER BY project_id", (item_id,))]


def assigned_projects(device_id):
    return [r[0] for r in db.conn().execute(
        "SELECT project_id FROM device_projects WHERE device_id = ? ORDER BY project_id", (device_id,))]


def _apks(upstream_id, tag):
    """apks.json of a tag dir, or None when missing or unreadable."""
    try:
        facts = json.loads((upstream.tag_dir(upstream_id, tag) / "apks.json").read_text())
    except (OSError, ValueError):
        return None
    return facts if isinstance(facts, list) else None


def _alert(r, facts):
    """Stored signer_alert, else the downloaded tag's signer when it differs from the pin: a job that stopped between
    the tag commit and after_download must not leave a new-signer release installable."""
    sig = facts[0].get("signer_sha256") if facts else None
    return r["signer_alert"] or (sig if r["signer_sha256"] and sig and sig != r["signer_sha256"] else None)


def _row(project_id):
    r = db.conn().execute("""SELECT p.*, u.repo, u.channel, u.asset_regex, u.latest_tag, u.latest_published_at,
        u.downloaded_tag, u.checked_at, u.check_error FROM project p JOIN upstream u ON u.id = p.upstream_id
        WHERE p.id = ?""", (project_id,)).fetchone()
    return dict(r) if r else None


def project_info(project_id, abis):
    r = _row(project_id)
    if r is None:
        return None
    tag = r["downloaded_tag"]
    facts = _apks(r["upstream_id"], tag) if tag else None
    if facts is None:
        tag = None
    pick = pick_apk(facts, abis) if facts else None
    path = os.path.abspath(upstream.tag_dir(r["upstream_id"], tag) / pick["file"]) if pick else None
    return {"project_id": r["id"], "upstream_id": r["upstream_id"], "package": r["package"], "display_name": label(r),
            "tag": tag, "path": path, **{k: pick[k] if pick else None for k in ("file", "abi", "version_name", "version_code")},
            "signer_alert": _alert(r, facts), "auto_push": bool(r["auto_push"])}


def check_assignment(item_ids, project_ids):
    c = db.conn()
    pkgs = []
    for i in item_ids:
        r = c.execute("SELECT package FROM item WHERE id = ?", (i,)).fetchone()
        if r is None:
            raise server.HttpError(400, f"unknown item {i}")
        pkgs.append(r[0])
    for p in project_ids:
        r = c.execute("SELECT package FROM project WHERE id = ?", (p,)).fetchone()
        if r is None:
            raise server.HttpError(400, f"unknown project {p}")
        if r[0]:  # unknown before the first download (§9 known limitation 1)
            pkgs.append(r[0])
    seen = set()
    for pkg in pkgs:
        if pkg in seen:
            raise server.HttpError(400, f"two apps with package {pkg}")
        seen.add(pkg)


# ---- release verification and signer pinning (§4.3) ----

def _tool(argv, log, parse, error):
    lines = []
    # parses an untrusted downloaded APK: isolated runner uid like the inspector
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o770)
        try:
            rc = jobs.run_isolated(log, argv, tmp, timeout=120, on_line=lines.append)
        except jobs.JobFailed as e:
            if str(e) == "cancelled":
                raise
            raise ValueError(str(e))
    value = parse("\n".join(lines))
    if value is None or rc != 0:
        raise ValueError(error)
    return value


def verify_release(upstream_id, part, log):
    apks = sorted(Path(part).glob("*.apk"))
    if not apks:
        raise ValueError("no APK in release")
    facts = []
    for apk in apks:
        abi = apk_abi(apk)
        sig = _tool([*APKSIGNER, "verify", "--print-certs", jobs.runner_path(apk)], log, parse_signer,
                    f"signer unreadable: {apk.name}")
        info = _tool([*APKEDITOR, "info", "-i", jobs.runner_path(apk), "-package", "-version-code", "-version-name"],
                     log, parse_apk_info, f"package unreadable: {apk.name}")
        facts.append({"file": apk.name, "abi": abi, "package": info["package"], "version_name": info["version_name"],
                      "version_code": info["version_code"], "signer_sha256": sig, "size": apk.stat().st_size})
    if len({f["signer_sha256"] for f in facts}) > 1:
        raise ValueError("signers differ within release")
    if len({f["package"] for f in facts}) > 1:
        raise ValueError("packages differ within release")
    pkg = facts[0]["package"]
    p = db.conn().execute("SELECT id, package FROM project WHERE upstream_id = ?", (upstream_id,)).fetchone()
    if p is not None and p["package"] and p["package"] != pkg:
        raise ValueError(f"package changed: {p['package']} -> {pkg}")
    other = db.conn().execute("SELECT id FROM project WHERE package = ? AND upstream_id != ?", (pkg, upstream_id)).fetchone()
    if other is not None:
        raise ValueError(f"package {pkg} already followed by project {other[0]}")
    for abi in sorted({f["abi"] for f in facts}):
        names = [f["file"] for f in facts if f["abi"] == abi]
        if len(names) > 1:
            log.line(f"[!] {len(names)} APKs for {abi}: {', '.join(names)}; installs use {names[0]}")
    (Path(part) / "apks.json").write_text(json.dumps(facts))
    return facts


def after_download(upstream_id, tag, log, job_id):
    facts = _apks(upstream_id, tag)
    if not facts:
        return
    sig, pkg = facts[0]["signer_sha256"], facts[0]["package"]
    with db.tx() as c:
        p = c.execute("SELECT * FROM project WHERE upstream_id = ?", (upstream_id,)).fetchone()
        if p is None:
            return
        pin, alert, changed = p["signer_sha256"], p["signer_alert"], False
        if p["package"] is None:
            c.execute("UPDATE project SET package = ? WHERE id = ?", (pkg, p["id"]))
        if pin is None:
            alert = None
            c.execute("UPDATE project SET signer_sha256 = ?, signer_since = ?, signer_alert = NULL WHERE id = ?",
                      (sig, tag, p["id"]))
        elif sig == pin:
            alert = None
            c.execute("UPDATE project SET signer_alert = NULL WHERE id = ?", (p["id"],))
        elif sig != alert:
            alert, changed = sig, True
            c.execute("UPDATE project SET signer_alert = ? WHERE id = ?", (sig, p["id"]))
    if pin is None:
        log.line(f"[+] signer pinned: {sig}")
    if changed:
        log.line(f"[!] signer changed: {pin} -> {sig}")
        events.emit("project.signer_changed", {"project_id": p["id"], "upstream_id": upstream_id, "tag": tag,
                                               "old_sha256": pin, "new_sha256": sig, "job_id": job_id})
    if alert is None and p["auto_push"]:
        row = jobs.get(job_id) if job_id is not None else None
        enqueue_auto_push(p["id"], row["options"].get("notify", True) if row else True)


def enqueue_auto_push(project_id, notify=True):
    return [jobs.enqueue("ota", {"device_id": d, "project_ids": [project_id]}, "upstream", options={"notify": notify})
            for (d,) in db.conn().execute(
                "SELECT device_id FROM device_projects WHERE project_id = ? ORDER BY device_id", (project_id,)).fetchall()]


# ---- routes (§6) ----

def _obj(project_id):
    r = _row(project_id)
    if r is None:
        raise server.HttpError(404, "project not found")
    uid = r["upstream_id"]
    base = db.DATA / "upstream" / str(uid)
    cur = upstream.tag_dir(uid, r["downloaded_tag"]).name if r["downloaded_tag"] else None
    dirs = sorted((d for d in base.iterdir() if d.is_dir() and not d.name.endswith(".part")),
                  key=lambda d: (d.name != cur, -d.stat().st_mtime)) if base.is_dir() else []
    versions = []
    for d in dirs:
        facts = _apks(uid, d.name)
        if facts is None:
            files = [{"name": a.name, "abi": None, "size": a.stat().st_size, "version_name": None, "version_code": None}
                     for a in sorted(d.glob("*.apk"))]
        else:
            files = [{"name": f["file"], "abi": f["abi"], "size": f["size"], "version_name": f["version_name"],
                      "version_code": f["version_code"]} for f in facts]
        versions.append({"tag": d.name, "current": d.name == cur, "files": files})
    c = db.conn()
    return {"id": r["id"], "upstream_id": uid, "repo": r["repo"], "channel": r["channel"],
            "asset_regex": r["asset_regex"], "display_name": r["display_name"], "label": label(r),
            "package": r["package"], "auto_push": bool(r["auto_push"]),
            "latest_tag": r["latest_tag"], "latest_published_at": r["latest_published_at"],
            "downloaded_tag": r["downloaded_tag"], "checked_at": r["checked_at"], "check_error": r["check_error"],
            "signer_sha256": r["signer_sha256"], "signer_since": r["signer_since"],
            "signer_alert": _alert(r, _apks(uid, r["downloaded_tag"]) if r["downloaded_tag"] else None),
            "versions": versions,
            "disk_bytes": sum(f.stat().st_size for f in base.rglob("*") if f.is_file()) if base.is_dir() else 0,
            "item_ids": [x[0] for x in c.execute(
                "SELECT item_id FROM item_dependency WHERE project_id = ? ORDER BY item_id", (r["id"],))],
            "device_ids": [x[0] for x in c.execute(
                "SELECT device_id FROM device_projects WHERE project_id = ? ORDER BY device_id", (r["id"],))]}


def _channel(v):
    if v not in ("stable", "prerelease"):
        raise server.HttpError(400, "invalid channel")
    return v


def _regex(v):
    if not isinstance(v, str) or len(v) > 300:
        raise server.HttpError(400, "invalid asset_regex")
    try:
        re.compile(v)
    except re.error:
        raise server.HttpError(400, "invalid asset_regex")
    return v


def _bool(v, field):
    if not isinstance(v, bool):
        raise server.HttpError(400, f"invalid {field}")
    return v


def _device_ids(v):
    if (not isinstance(v, list) or len(set(map(str, v))) != len(v)
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
        raise server.HttpError(400, "invalid device_ids")
    for d in v:
        if not db.conn().execute("SELECT 1 FROM devices WHERE id = ?", (d,)).fetchone():
            raise server.HttpError(400, f"unknown device {d}")
    return v


def _release(repo, channel, rx, no_match):
    """(release, matched assets) through upstream.fetch; GitHub errors become HTTP errors (never 401), no matching
    asset becomes 400 no_match."""
    try:
        return upstream.fetch({"kind": "project", "repo": repo, "channel": channel, "tag": None, "asset_regex": rx})
    except upstream.TokenRejected:
        raise server.HttpError(502, "token rejected")
    except upstream.RateLimited as e:
        raise server.HttpError(429, str(e))
    except upstream.NoMatchingAsset:
        raise server.HttpError(400, no_match)
    except upstream.CheckError as e:
        raise server.HttpError(400, str(e))


def _set_devices(c, project_id, device_ids):
    """Replace the project's device rows; the caller's transaction sees the new project row."""
    for d in device_ids:
        items = [r[0] for r in c.execute("SELECT item_id FROM device_assignments WHERE device_id = ?", (d,))]
        check_assignment(items, sorted(set(assigned_projects(d)) | {project_id}))
    c.execute("DELETE FROM device_projects WHERE project_id = ?", (project_id,))
    c.executemany("INSERT INTO device_projects(device_id, project_id) VALUES (?, ?)", [(d, project_id) for d in device_ids])


def _enqueue_pull(upstream_id):
    return jobs.enqueue("upstream", {"upstream_id": upstream_id}, "manual", options={"rebuild": False})


def list_projects(req):
    return 200, {"projects": [_obj(r[0]) for r in db.conn().execute("SELECT id FROM project ORDER BY id").fetchall()]}


def post_assets(req):
    b = req.json or {}
    repo, channel = server.validate_repo(b.get("repo"), "repo"), _channel(b.get("channel"))
    rel, assets = _release(repo, channel, r"\.apk$", "no APK asset in latest release")
    names = [a["name"] for a in assets]
    rx = None
    if b.get("selected") is not None:
        sel = b["selected"]
        if not isinstance(sel, list) or not all(isinstance(s, str) for s in sel):
            raise server.HttpError(400, "invalid selected")
        for s in sel:
            if s not in names:
                raise server.HttpError(400, f"unknown asset {s}")
        rx = derive_regex(sel) if sel else None
    elif b.get("asset_regex") is not None:
        rx = _regex(b["asset_regex"])
    return 200, {"tag": rel["tag_name"], "published_at": rel.get("published_at"), "asset_regex": rx,
                 "assets": [{"name": a["name"], "size": a.get("size"), "matched": bool(rx and re.search(rx, a["name"]))}
                            for a in assets]}


def _exists(repo, channel):
    return db.conn().execute("""SELECT 1 FROM project p JOIN upstream u ON u.id = p.upstream_id
        WHERE u.repo = ? AND u.channel = ? AND u.tag IS NULL""", (repo, channel)).fetchone() is not None


def post_project(req):
    b = req.json or {}
    repo, channel, rx = server.validate_repo(b.get("repo"), "repo"), _channel(b.get("channel")), _regex(b.get("asset_regex"))
    dn = catalog._display_name(b.get("display_name"))
    auto = _bool(b.get("auto_push", False), "auto_push")
    devs = _device_ids(b.get("device_ids", []))
    if _exists(repo, channel):
        raise server.HttpError(409, "project exists")
    _, assets = _release(repo, channel, rx, "asset_regex matches no asset")
    if not any(a["name"].endswith(".apk") for a in assets):
        raise server.HttpError(400, "asset_regex matches no asset")
    with db.tx() as c:
        if _exists(repo, channel):
            raise server.HttpError(409, "project exists")
        uid = catalog.upstream_row(c, "project", repo, channel, None, rx)
        pid = c.execute("INSERT INTO project(upstream_id, display_name, auto_push) VALUES (?, ?, ?)",
                        (uid, dn, int(auto))).lastrowid
        _set_devices(c, pid, devs)
    return 201, {"id": pid, "job_id": _enqueue_pull(uid)}


def put_project(req):
    pid = req.params["id"]
    cur = _obj(pid)
    b = req.json or {}
    for fixed in ("repo", "package"):
        if fixed in b and b[fixed] != cur[fixed]:
            raise server.HttpError(400, f"{fixed} cannot change")
    proj, up = {}, {}
    if "display_name" in b:
        proj["display_name"] = catalog._display_name(b["display_name"])
    if "auto_push" in b:
        proj["auto_push"] = int(_bool(b["auto_push"], "auto_push"))
    if "asset_regex" in b and _regex(b["asset_regex"]) != cur["asset_regex"]:
        up.update(asset_regex=b["asset_regex"], downloaded_tag=None)
    if "channel" in b and _channel(b["channel"]) != cur["channel"]:
        up.update(channel=b["channel"], latest_tag=None, downloaded_tag=None, notified_tag=None)
    devs = _device_ids(b["device_ids"]) if "device_ids" in b else None
    try:
        with db.tx() as c:
            for col, val in proj.items():  # col from the fixed names above
                c.execute(f"UPDATE project SET {col} = ? WHERE id = ?", (val, pid))
            for col, val in up.items():
                c.execute(f"UPDATE upstream SET {col} = ? WHERE id = ?", (val, cur["upstream_id"]))
            if devs is not None:
                _set_devices(c, pid, devs)
    except sqlite3.IntegrityError:
        raise server.HttpError(409, "project exists")
    if up:
        _enqueue_pull(cur["upstream_id"])
    return 200, _obj(pid)


def delete_project(req):
    pid = req.params["id"]
    cur = _obj(pid)
    uid = cur["upstream_id"]
    if cur["item_ids"]:
        raise server.HttpError(409, "project is a dependency of items")
    if db.conn().execute("SELECT 1 FROM jobs WHERE kind = 'upstream' AND status IN ('queued', 'running') AND target = ?",
                         (jobs.canon({"upstream_id": uid}),)).fetchone():
        raise server.HttpError(409, "pull queued or running")
    with db.tx() as c:
        c.execute("DELETE FROM project WHERE id = ?", (pid,))
        c.execute("DELETE FROM upstream WHERE id = ?", (uid,))
    shutil.rmtree(db.DATA / "upstream" / str(uid), ignore_errors=True)
    return 200, {"ok": True}


def accept_signer(req):
    pid = req.params["id"]
    cur = _obj(pid)
    with db.tx():  # write lock: after_download cannot set a newer alert between this read and the update
        r = _row(pid)
        alert = _alert(r, _apks(r["upstream_id"], r["downloaded_tag"]) if r["downloaded_tag"] else None)
        if alert is None:
            raise server.HttpError(409, "no signer change")
        if (req.json or {}).get("signer_sha256") != alert:
            raise server.HttpError(409, "signer alert changed, reload")
        db.conn().execute("UPDATE project SET signer_sha256 = ?, signer_since = ?, signer_alert = NULL WHERE id = ?",
                          (alert, r["downloaded_tag"], pid))
    if cur["auto_push"]:
        enqueue_auto_push(pid)
    return 200, _obj(pid)


ROUTES = [
    ("GET", "/api/projects", list_projects),
    ("POST", "/api/projects", post_project),
    ("POST", "/api/projects/assets", post_assets),
    ("PUT", "/api/projects/{id}", put_project),
    ("DELETE", "/api/projects/{id}", delete_project),
    ("POST", "/api/projects/{id}/signer/accept", accept_signer),
]
