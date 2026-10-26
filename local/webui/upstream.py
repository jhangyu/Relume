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
from urllib.parse import urlsplit

from . import catalog, db, events, jobs, projects, server, settings

API = "https://api.github.com"
DEFAULT_REGEX = {"patches": r"\.mpp$", "cli": r"^morphe-desktop-.*-all\.jar$", "module": r"\.apk$", "project": r"\.apk$"}


class TokenRejected(Exception):
    pass


class RateLimited(Exception):
    pass


class CheckError(Exception):
    pass


class NoMatchingAsset(CheckError):
    pass


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Drop Authorization when a redirect changes scheme or host:port (the stdlib copies it everywhere)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        old, nxt = urlsplit(req.full_url), urlsplit(new.full_url)
        if (old.scheme, old.netloc) != (nxt.scheme, nxt.netloc):
            new.remove_header("Authorization")
        return new


opener = urllib.request.build_opener(_SameOriginRedirect)


def http_get(url, headers=None, timeout=30, max_bytes=None):
    """(status, lower-cased headers, body, final URL after redirects). HTTP errors are returned; network errors raise
    OSError; a body over max_bytes raises ValueError."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        r = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        body = r.read() if max_bytes is None else r.read(max_bytes + 1)
        if max_bytes is not None and len(body) > max_bytes:
            raise ValueError(f"{url}: body over {max_bytes} bytes")
        return r.status, {k.lower(): v for k, v in r.headers.items()}, body, r.geturl()


def tag_dir(upstream_id, tag):
    return db.DATA / "upstream" / str(upstream_id) / server.safe_remote(tag)


def gh_headers(token):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "Relume"}
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
        if row["channel"] == "stable" and row["kind"] != "project" and "dev" in name:
            continue
        out.append(a)
    return out


def _release_url(row):
    if row["channel"] == "stable":
        return f"{API}/repos/{row['repo']}/releases/latest"
    if row["channel"] == "prerelease":
        return f"{API}/repos/{row['repo']}/releases?per_page=10"
    return f"{API}/repos/{row['repo']}/releases/tags/{row['tag']}"


def fetch(row):
    try:
        status, headers, body, _ = http_get(_release_url(row), gh_headers(settings.github_token()))
    except OSError as e:
        raise CheckError(("network error: " + str(e).splitlines()[0]) if str(e) else "network error")
    if status == 401:
        raise TokenRejected("token rejected")
    if status in (403, 429):
        reset = headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            raise RateLimited("rate limited until " +
                              db.iso(datetime.fromtimestamp(int(reset), timezone.utc)))
        raise RateLimited("rate limited")
    if status == 404:
        raise CheckError("not found")
    if status != 200:
        raise CheckError(f"http {status}")
    rel = select_release(row["channel"], json.loads(body))
    if rel is None:
        raise CheckError("no release")
    assets = match_assets(row, rel)
    if not assets:
        raise NoMatchingAsset("no matching asset")
    if row["kind"] == "module" and len(assets) != 1:
        raise CheckError(f"ambiguous module asset ({len(assets)} matches)")
    return rel, assets


def affected_items(upstream_id):
    """Enabled items whose bundle uses the row, whose engine uses it as CLI, or that depend on its project."""
    r = db.conn().execute("SELECT repo, channel, tag FROM upstream WHERE id = ?", (upstream_id,)).fetchone()
    engines = [e for e, cli in catalog.ENGINE_CLI.items() if r is not None and cli[:3] == tuple(r)]   # read only
    rows = db.conn().execute(f"""SELECT i.id FROM item i JOIN bundle b ON b.id = i.bundle_id
        WHERE i.enabled = 1 AND (b.upstream_id = ? OR b.engine IN ({','.join('?' * len(engines))}))
        UNION SELECT i.id FROM item i JOIN item_dependency x ON x.item_id = i.id JOIN project p ON p.id = x.project_id
        WHERE i.enabled = 1 AND p.upstream_id = ? ORDER BY 1""", (upstream_id, *engines, upstream_id)).fetchall()
    return [r[0] for r in rows]


def _check(upstream_id, job_id=None):
    """Check one row. Returns (row, assets); assets is None when check_error was set.
    TokenRejected / RateLimited are recorded, then re-raised."""
    row = _row(upstream_id)
    now = db.utcnow()
    try:
        rel, assets = fetch(row)
    except (TokenRejected, RateLimited) as e:
        _set(upstream_id, checked_at=now, check_error=str(e))
        raise
    except CheckError as e:
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



INSPECT = str(db.REPO_ROOT / "src" / "build" / "inspect.sh")
LOG_PREFIX_RE = re.compile(r"^(?:INFO|WARNING|SEVERE): ")
KNOWN_KEYS = {"Index", "Name", "Description", "Enabled", "Options", "Title", "Required", "Key", "Default",
              "Possible values", "Type", "Compatible packages", "Package name", "Compatible versions"}


def parse_list_patches(text):
    """Parse `list-patches --with-packages --with-versions --with-options` text (layout of the utils.sh list-patches output)."""
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
    cli = _row(catalog.engine_cli("morphe"))
    jars = sorted(tag_dir(cli["id"], cli["downloaded_tag"]).glob("*.jar")) if cli["downloaded_tag"] else []
    if not jars:
        log.line("[!] inspect skipped: CLI not downloaded")
        return
    for pkg in _packages(upstream_id):
        with tempfile.TemporaryDirectory() as out:
            # third-party CLI and patch bundle code: isolated runner uid, same gid writes into out
            os.chmod(out, 0o770)
            argv = ["bash", INSPECT, jobs.runner_path(jars[0]), pkg, out, *(jobs.runner_path(f) for f in files)]
            try:
                rc = jobs.run_isolated(log, argv, out, timeout=120)
            except jobs.JobFailed as e:
                if str(e) == "cancelled":
                    raise
                rc = str(e)
            if rc != 0:
                log.line(f"[!] inspect failed for {pkg}: {rc}")
                continue
            try:  # runner-written files: no symlink, one link
                with jobs.open_runner_file(out, "list-patches.txt") as f:
                    lp = f.read().decode("utf-8", "replace")
                with jobs.open_runner_file(out, "options.json") as f:
                    oj = f.read().decode("utf-8", "replace")
            except OSError as e:
                log.line(f"[!] inspect failed for {pkg}: {e}")
                continue
        patches = merge_bundle(lp, oj, pkg)
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO bundle_info(upstream_id, tag, package, patches) VALUES (?, ?, ?, ?)",
                      (upstream_id, tag, pkg, json.dumps(patches)))
        log.line(f"[+] inspected {pkg}: {len(patches)} patches")


def http_download(url, dest, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "Relume"})
    n = 0
    with opener.open(req, timeout=timeout) as r, open(dest, "wb") as f:
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


def _download(row, tag, assets, log, verify=None):
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
        if verify:
            verify(part)
    except (OSError, ValueError) as e:
        shutil.rmtree(part, ignore_errors=True)
        _set(row["id"], check_error=f"download failed: {e}")
        raise jobs.JobFailed(f"download failed: {e}")
    shutil.rmtree(final, ignore_errors=True)
    part.rename(final)
    _set(row["id"], downloaded_tag=tag)
    log.line(f"[+] {row['repo']} {tag} downloaded")
    return final


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
    with db.tx() as c:   # bundle_info of pruned tags
        for (tag,) in c.execute("SELECT DISTINCT tag FROM bundle_info WHERE upstream_id = ?", (upstream_id,)).fetchall():
            if tag_dir(upstream_id, tag).name not in keep:
                c.execute("DELETE FROM bundle_info WHERE upstream_id = ? AND tag = ?", (upstream_id, tag))


def _packages(upstream_id):
    return [r[0] for r in db.conn().execute("""SELECT DISTINCT i.package FROM item i JOIN bundle b ON b.id = i.bundle_id
        WHERE b.upstream_id = ? ORDER BY 1""", (upstream_id,))]


def _inspected(row):
    if row["kind"] != "patches":
        return True
    have = {r[0] for r in db.conn().execute("SELECT package FROM bundle_info WHERE upstream_id = ? AND tag = ?",
                                            (row["id"], row["downloaded_tag"]))}
    return set(_packages(row["id"])) <= have


def _fetch_row(upstream_id, log, job_id=None):
    """Pull steps 1-2 for one row. True when a new tag was downloaded. TokenRejected / RateLimited propagate."""
    row, assets = _check(upstream_id, job_id)
    if assets is None:
        raise jobs.JobFailed(row["check_error"])
    if row["latest_tag"] == row["downloaded_tag"] and tag_dir(upstream_id, row["downloaded_tag"]).is_dir():
        # a crash after the tag commit leaves no pin: pin it now, before a later release is pinned instead
        if row["kind"] == "project" and db.conn().execute(
                "SELECT 1 FROM project WHERE upstream_id = ? AND signer_sha256 IS NULL", (upstream_id,)).fetchone():
            projects.after_download(upstream_id, row["downloaded_tag"], log, job_id)
        return False
    verify = None
    if row["kind"] == "project":
        verify = lambda part: projects.verify_release(upstream_id, part, log)  # noqa: E731
    _download(row, row["latest_tag"], assets, log, verify)
    if verify:
        projects.after_download(upstream_id, row["latest_tag"], log, job_id)
    _prune(upstream_id)
    return True


def pull(upstream_id, log, job_id=None):
    """Check, download, prune and inspect one row. True when a new tag was downloaded. A row failure raises
    jobs.JobFailed; TokenRejected / RateLimited propagate so a caller looping over rows can stop its GitHub step."""
    try:
        row = _row(upstream_id)
    except KeyError:
        raise jobs.JobFailed("upstream not found")
    log.line(f"[+] pull {row['repo']} ({row['channel']}{' ' + row['tag'] if row['tag'] else ''})")
    new = _fetch_row(upstream_id, log, job_id)
    row = _row(upstream_id)
    if not new and _inspected(row):
        log.line("[+] up to date")
        return False
    if row["kind"] == "patches":
        cli_id = catalog.engine_cli("morphe")
        if _row(cli_id)["downloaded_tag"] is None:
            log.line(f"[+] CLI upstream {cli_id} not downloaded, pulling it first")
            _fetch_row(cli_id, log, job_id)
        inspect_row(upstream_id, row["downloaded_tag"], log)
    return new


def _handler(job, log):
    """{upstream_id}: one pull (Follow projects, OTA). {all} / {item_id}: the update run of apps.py."""
    if "upstream_id" in job.target:
        try:
            pull(job.target["upstream_id"], log, job_id=job.id)
        except (TokenRejected, RateLimited) as e:
            raise jobs.JobFailed(str(e))
        return
    from . import apps   # apps imports this module
    apps.run_update(job, log)


def register():
    jobs.register("upstream", _handler)
