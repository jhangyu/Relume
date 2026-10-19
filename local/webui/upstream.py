"""Upstream release tracking: check, pull, inspect (spec catalog §5)."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from webui import catalog, db, events, jobs, server, settings

API = "https://api.github.com"
DEFAULT_REGEX = {"patches": r"\.mpp$", "cli": r"^morphe-desktop-.*-all\.jar$", "module": r"\.apk$", "dependency": r"\.apk$"}


class TokenRejected(Exception):
    pass


class RateLimited(Exception):
    pass


class _CheckError(Exception):
    pass


def http_get(url, headers=None, timeout=30):
    """(status, lower-cased headers, body). HTTP errors are returned; network errors raise OSError."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def tag_dir(upstream_id, tag):
    return db.DATA / "upstream" / str(upstream_id) / server.safe_remote(tag)


def _gh_headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "Patchbay"}
    token = settings.github_token()
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _row(upstream_id):
    r = db.conn().execute("SELECT * FROM upstream WHERE id = ?", (upstream_id,)).fetchone()
    if r is None:
        raise KeyError(upstream_id)
    return dict(r)


def _set(upstream_id, **cols):
    # column names come from this module only
    with db.tx() as c:
        c.execute("UPDATE upstream SET " + ", ".join(f"{k} = ?" for k in cols) + " WHERE id = ?",
                  (*cols.values(), upstream_id))


def select_release(channel, data):
    if channel == "prerelease":
        rels = [r for r in data if isinstance(r, dict) and not r.get("draft")]
        return max(rels, key=lambda r: r.get("published_at") or "", default=None)
    return None if not isinstance(data, dict) or data.get("draft") else data


def match_assets(row, release):
    rx = re.compile(row["asset_regex"] or DEFAULT_REGEX[row["kind"]])
    out = []
    for a in release.get("assets", []):
        name = a.get("name", "")
        if name.endswith(".asc") or not rx.search(name):
            continue
        if row["channel"] == "stable" and "dev" in name:
            continue
        out.append(a)
    return out


def _release_url(row):
    if row["channel"] == "stable":
        return f"{API}/repos/{row['repo']}/releases/latest"
    if row["channel"] == "prerelease":
        return f"{API}/repos/{row['repo']}/releases?per_page=10"
    return f"{API}/repos/{row['repo']}/releases/tags/{row['tag']}"


def _fetch(row):
    try:
        status, headers, body = http_get(_release_url(row), _gh_headers())
    except OSError as e:
        raise _CheckError(("network error: " + str(e).splitlines()[0]) if str(e) else "network error")
    if status == 401:
        raise TokenRejected("token rejected")
    if status in (403, 429):
        reset = headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            raise RateLimited("rate limited until " +
                              datetime.fromtimestamp(int(reset), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        raise RateLimited("rate limited")
    if status == 404:
        raise _CheckError("not found")
    if status != 200:
        raise _CheckError(f"http {status}")
    rel = select_release(row["channel"], json.loads(body))
    if rel is None:
        raise _CheckError("no release")
    assets = match_assets(row, rel)
    if not assets:
        raise _CheckError("no matching asset")
    if row["kind"] == "module" and len(assets) != 1:
        raise _CheckError(f"ambiguous module asset ({len(assets)} matches)")
    return rel, assets


def affected_items(upstream_id):
    rows = db.conn().execute("""SELECT i.id FROM item i JOIN source s ON s.id = i.source_id
        WHERE s.upstream_id = ? OR s.cli_upstream_id = ?
        UNION SELECT x.item_id FROM item_dependency x JOIN dependency_app d ON d.id = x.dependency_app_id
        WHERE d.upstream_id = ? ORDER BY 1""", (upstream_id, upstream_id, upstream_id)).fetchall()
    return [r[0] for r in rows]


def _check(upstream_id, job_id=None):
    """Check one row. Returns (row, assets); assets is None when check_error was set.
    TokenRejected / RateLimited are recorded, then re-raised."""
    row = _row(upstream_id)
    now = catalog.now_iso()
    try:
        rel, assets = _fetch(row)
    except (TokenRejected, RateLimited) as e:
        _set(upstream_id, checked_at=now, check_error=str(e))
        raise
    except _CheckError as e:
        _set(upstream_id, checked_at=now, check_error=str(e))
        return _row(upstream_id), None
    tag = rel["tag_name"]
    _set(upstream_id, latest_tag=tag, latest_published_at=rel.get("published_at"), checked_at=now, check_error=None)
    if tag != row["notified_tag"]:
        _set(upstream_id, notified_tag=tag)
        events.emit("upstream.new_version", {"upstream_id": upstream_id, "tag": tag,
                                             "item_ids": affected_items(upstream_id), "job_id": job_id})
    return _row(upstream_id), assets


def check(upstream_id, job_id=None):
    return _check(upstream_id, job_id)[0]


def _all_rows():
    rows = [dict(r) for r in db.conn().execute("SELECT * FROM upstream ORDER BY id")]
    for r in rows:
        r["source_ids"] = [x[0] for x in db.conn().execute(
            "SELECT id FROM source WHERE upstream_id = ? OR cli_upstream_id = ? ORDER BY id", (r["id"], r["id"]))]
    return rows


def check_all():
    for (uid,) in db.conn().execute("SELECT id FROM upstream ORDER BY id").fetchall():
        try:
            check(uid)
        except (TokenRejected, RateLimited):
            break
    return _all_rows()


def list_upstreams(req):
    return 200, {"upstreams": _all_rows()}


def post_check(req):
    return 200, {"upstreams": check_all()}



INSPECT = str(catalog.REPO_ROOT / "src" / "build" / "inspect.sh")
LOG_PREFIX_RE = re.compile(r"^(?:INFO|WARNING|SEVERE): ")
KNOWN_KEYS = {"Index", "Name", "Description", "Enabled", "Options", "Title", "Required", "Key", "Default",
              "Possible values", "Type", "Compatible packages", "Package name", "Compatible versions"}


def parse_list_patches(text):
    """Parse `list-patches --with-packages --with-versions --with-options` text (layout of utils.sh:195-201)."""
    patches, cur, opt, pkg, mode, sub = [], None, None, None, None, None
    for raw in text.splitlines():
        # the CLI logger can glue its level prefix onto the first block ("INFO: Index: 0", captured fixture)
        line = LOG_PREFIX_RE.sub("", raw.strip())
        if not line:
            continue
        key, sep, val = line.partition(":")
        key, val = key.strip(), val.strip()
        known = bool(sep) and key in KNOWN_KEYS
        if known and key == "Index":
            cur = {"name": None, "description": "", "enabled": None, "options": [], "packages": {}}
            patches.append(cur)
            opt = pkg = mode = sub = None
            continue
        if cur is None:
            continue
        if not known:
            if sub == "values" and opt is not None:
                opt["values"].append(line)
            elif sub == "versions" and pkg is not None and line[:1].isdigit():
                cur["packages"][pkg].append(line.split()[0])
            continue
        sub = None
        if key == "Options":
            mode = "options"
            continue
        if key in ("Compatible packages", "Package name"):
            mode = "packages"
            if key == "Package name":
                pkg = val
                cur["packages"].setdefault(pkg, [])
            continue
        if mode == "options":
            if opt is None or (key in ("Title", "Key") and opt[key.lower()] is not None):
                opt = {"key": None, "title": None, "type": None, "default": None, "values": []}
                cur["options"].append(opt)
            if key == "Title":
                opt["title"] = val
            elif key == "Key":
                opt["key"] = val
            elif key == "Type":
                opt["type"] = val
            elif key == "Default":
                opt["default"] = val or None
            elif key == "Possible values":
                sub = "values"
            continue
        if mode == "packages":
            if key == "Compatible versions" and pkg is not None:
                sub = "versions"
            continue
        if key == "Name":
            cur["name"] = val
        elif key == "Description":
            cur["description"] = val
        elif key == "Enabled":
            cur["enabled"] = val.lower() == "true"
    return patches


def merge_bundle(list_text, options_text, package):
    try:
        defaults = json.loads(options_text)[0].get("patches", {})
    except (ValueError, IndexError, KeyError, AttributeError, TypeError):
        defaults = {}
    out = []
    for p in parse_list_patches(list_text):
        if not p["name"]:
            continue
        d = defaults.get(p["name"]) if isinstance(defaults.get(p["name"]), dict) else {}
        opt_defaults = d.get("options", {}) if isinstance(d.get("options"), dict) else {}
        out.append({"name": p["name"], "description": p["description"],
                    "default_enabled": d.get("enabled", p["enabled"]),
                    "options": [dict(o, default=opt_defaults.get(o["key"], o["default"])) for o in p["options"]],
                    "compatible_versions": sorted(p["packages"].get(package, []), key=catalog.version_key, reverse=True)})
    return out


def inspect_row(upstream_id, tag, log):
    files = sorted(str(p) for p in tag_dir(upstream_id, tag).iterdir() if p.is_file())
    clis = [r[0] for r in db.conn().execute(
        "SELECT DISTINCT cli_upstream_id FROM source WHERE upstream_id = ? ORDER BY 1", (upstream_id,))]
    if not clis:
        return
    cli = _row(clis[0])
    jars = sorted(tag_dir(cli["id"], cli["downloaded_tag"]).glob("*.jar")) if cli["downloaded_tag"] else []
    if not jars:
        log.line("[!] inspect skipped: CLI not downloaded")
        return
    for pkg in _packages(upstream_id):
        with tempfile.TemporaryDirectory() as out:
            try:
                rc = log.run(["bash", INSPECT, str(jars[0]), pkg, out, *files], cwd=out, env=_child_env(), timeout=120)
            except jobs.JobFailed as e:
                if str(e) == "cancelled":
                    raise
                rc = str(e)
            if rc != 0:
                log.line(f"[!] inspect failed for {pkg}: {rc}")
                continue
            lp = Path(out, "list-patches.txt").read_text(errors="replace")
            oj = Path(out, "options.json").read_text(errors="replace")
        patches = merge_bundle(lp, oj, pkg)
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO bundle_info(upstream_id, tag, package, patches) VALUES (?, ?, ?, ?)",
                      (upstream_id, tag, pkg, json.dumps(patches)))
        log.line(f"[+] inspected {pkg}: {len(patches)} patches")


APKSIGNER = ["apksigner"]


def http_download(url, dest, headers=None, timeout=300):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Patchbay"})
    n = 0
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            n += len(chunk)
    return n


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _child_env():
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TZ") if k in os.environ}


def _download(row, tag, assets, log):
    final = tag_dir(row["id"], tag)
    part = final.with_name(final.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    try:
        for a in assets:
            dest = part / server.safe_remote(a["name"])
            log.line(f"[+] downloading {a['name']}")
            http_download(a["browser_download_url"], dest)
            if dest.stat().st_size != a.get("size"):
                raise ValueError(f"size mismatch: {a['name']}")
            digest = a.get("digest") or ""
            if digest.startswith("sha256:") and _sha256(dest) != digest[7:]:
                raise ValueError(f"hash mismatch: {a['name']}")
    except (OSError, ValueError) as e:
        shutil.rmtree(part, ignore_errors=True)
        _set(row["id"], check_error=f"download failed: {e}")
        raise jobs.JobFailed(f"download failed: {e}")
    shutil.rmtree(final, ignore_errors=True)
    part.rename(final)
    _set(row["id"], downloaded_tag=tag)
    log.line(f"[+] {row['repo']} {tag} downloaded")
    return final


def _write_signer(final, log):
    apks = sorted(final.glob("*.apk"))
    if not apks:
        return
    found = []

    def on_line(text):
        m = re.match(r"Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)$", text.strip())
        if m:
            found.append(m.group(1).lower())

    log.run([*APKSIGNER, "verify", "--print-certs", str(apks[0])], cwd=str(final), env=_child_env(), on_line=on_line)
    if found:
        (final / "signer.sha256").write_text(found[0] + "\n")
    else:
        log.line(f"[!] no signer digest for {apks[0].name}")


def _prune(upstream_id):
    base = db.DATA / "upstream" / str(upstream_id)
    cur = _row(upstream_id)["downloaded_tag"]
    keep = [tag_dir(upstream_id, cur).name] if cur else []
    dirs = sorted((d for d in base.iterdir() if d.is_dir() and not d.name.endswith(".part")),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    for d in dirs:
        if d.name in keep:
            continue
        if len(keep) < 2:
            keep.append(d.name)
        else:
            shutil.rmtree(d, ignore_errors=True)


def _packages(upstream_id):
    return [r[0] for r in db.conn().execute("""SELECT DISTINCT i.package FROM item i JOIN source s ON s.id = i.source_id
        WHERE s.upstream_id = ? ORDER BY 1""", (upstream_id,))]


def _inspected(row):
    if row["kind"] != "patches":
        return True
    have = {r[0] for r in db.conn().execute("SELECT package FROM bundle_info WHERE upstream_id = ? AND tag = ?",
                                            (row["id"], row["downloaded_tag"]))}
    return set(_packages(row["id"])) <= have


def _fetch_row(upstream_id, log, job_id=None):
    """Pull steps 1-2 for one row. True when a new tag was downloaded."""
    try:
        row, assets = _check(upstream_id, job_id)
    except (TokenRejected, RateLimited) as e:
        raise jobs.JobFailed(str(e))
    if assets is None:
        raise jobs.JobFailed(row["check_error"])
    if row["latest_tag"] == row["downloaded_tag"] and tag_dir(upstream_id, row["downloaded_tag"]).is_dir():
        return False
    final = _download(row, row["latest_tag"], assets, log)
    if row["kind"] == "dependency":
        _write_signer(final, log)
    _prune(upstream_id)
    return True

def pull(upstream_id, log, rebuild=False, job_id=None):
    try:
        row = _row(upstream_id)
    except KeyError:
        raise jobs.JobFailed("upstream not found")
    log.line(f"[+] pull {row['repo']} ({row['channel']}{' ' + row['tag'] if row['tag'] else ''})")
    new = _fetch_row(upstream_id, log, job_id)
    row = _row(upstream_id)
    if not new and _inspected(row):
        log.line("[+] up to date")
        return
    if row["kind"] == "patches":
        for (cli_id,) in db.conn().execute("SELECT DISTINCT cli_upstream_id FROM source WHERE upstream_id = ?",
                                           (upstream_id,)).fetchall():
            if _row(cli_id)["downloaded_tag"] is None:
                log.line(f"[+] CLI upstream {cli_id} not downloaded, pulling it first")
                _fetch_row(cli_id, log, job_id)
        inspect_row(upstream_id, row["downloaded_tag"], log)
    if new and rebuild and row["kind"] in ("patches", "module"):
        ids = [r[0] for r in db.conn().execute("""SELECT i.id FROM item i JOIN source s ON s.id = i.source_id
            WHERE s.upstream_id = ? AND i.enabled = 1 ORDER BY i.id""", (upstream_id,))]
        for iid in ids:
            jobs.enqueue("build", {"item_id": iid}, "upstream")
        log.line(f"[+] rebuild enqueued for {len(ids)} item(s)")


def check_only(upstream_id, log, job_id=None):
    """Scheduled job with options.pull false: check (5.1) without download or inspect (spec L324)."""
    try:
        row = check(upstream_id, job_id)
    except KeyError:
        raise jobs.JobFailed("upstream not found")
    except (TokenRejected, RateLimited) as e:
        raise jobs.JobFailed(str(e))
    if row["check_error"]:
        raise jobs.JobFailed(row["check_error"])
    log.line(f"[+] check only: {row['repo']} latest {row['latest_tag']}, downloaded {row['downloaded_tag']}")


def _handler(job, log):
    opts = job.options or {}
    if opts.get("pull", True) is False:
        check_only(job.target["upstream_id"], log, job_id=job.id)
        return
    pull(job.target["upstream_id"], log, rebuild=bool(opts.get("rebuild", False)), job_id=job.id)


def register():
    jobs.register("upstream", _handler)


def post_pull(req):
    uid = req.params["id"]
    if not db.conn().execute("SELECT 1 FROM upstream WHERE id = ?", (uid,)).fetchone():
        raise server.HttpError(404, "upstream not found")
    opts = req.json if isinstance(req.json, dict) else {}
    return 200, {"job_id": jobs.enqueue("upstream", {"upstream_id": uid}, "manual", options=opts)}


ROUTES = [
    ("GET", "/api/upstreams", list_upstreams),
    ("POST", "/api/upstreams/check", post_check),
    ("POST", "/api/upstreams/{id}/pull", post_pull),
]
