"""Catalog layer (marketplace spec §2): feeds, parsers, apply, built-in and custom sources, patch lists, icons."""
import html
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import db, server, upstream

ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64", "universal")
UNIVERSAL = '*'
FEED_RANK = {"builtin": 0, "custom": 1, "official": 2}   # lower wins on de-dup
ICON_HOSTS = frozenset({"play-lh.googleusercontent.com", "lh3.googleusercontent.com", "lh3.ggpht.com",
                        "raw.githubusercontent.com", "avatars.githubusercontent.com"})
ICON_MAX_BYTES = 256 * 1024
PLAY_PAGE_MAX_BYTES = 4 * 1024 * 1024
FEED_MAX_BYTES = 16 * 1024 * 1024
PLAY_ICON_SIZE = "=s256-rw"
ICON_TIMEOUT = 10
FEED_TIMEOUT = 30
GITLAB_REASON = "GitLab releases not supported"
ENGINE_CLI = {"morphe": ("MorpheApp/morphe-desktop", "stable", None, r"^morphe-desktop-.*-all\.jar$"),
              "lspatch": ("JingMatrix/LSPatch", "stable", None, r"^lspatch-.*-release\.jar$"),
              "npatch": ("7723mod/NPatch", "tag", "v1.0.5", r"^jar-.*-release\.jar$")}
ENGINES = {'morphe': {'kind': 'patches'},
           'lspatch': {'kind': 'module', 'max_sigbypass': 3},
           'npatch': {'kind': 'module', 'max_sigbypass': 4}}
MAX_MODULE_PACKAGES = 20
GITLAB_REPO = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+$")   # GitLab allows nested groups
LIST_URL = "https://raw.githubusercontent.com/{}/{}/patches-list.json"
PLAY_URL = "https://play.google.com/store/apps/details?id={}&hl=en"
PLAY_HOST = "play.google.com"
OG_IMAGE_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"')
SIZE_SUFFIX_RE = re.compile(r"=[^=/]*$")
ICON_EXT = {"image/png": "png", "image/webp": "webp", "image/jpeg": "jpg"}


class CatalogInvalid(ValueError):
    """A feed or patch list breaks the accepted grammar: '<path>: expected <x>, got <y>'."""


class CatalogRefreshFailed(Exception):
    """Some feed or list failed; everything that succeeded is applied."""


class CatalogRefreshBusy(Exception):
    """Another refresh holds the lock."""


class UpstreamMissing(Exception):
    """A needed upstream has no downloaded tag (build fails upstream_missing)."""


@dataclass(frozen=True)
class Patch:
    name: str
    description: str | None
    default_enabled: bool | None
    packages: tuple[str, ...]   # () = universal


@dataclass(frozen=True)
class Bundle:
    host: str
    repo: str
    branch: str | None
    name: str
    author: str
    description: str | None
    stars: int
    updated_at: str | None
    apps: dict   # package -> versions newest first; () = any version
    patches: tuple[Patch, ...]


@dataclass(frozen=True)
class StoreApp:
    package: str
    name: str
    category: str
    description: str | None
    icon_url: str | None


@dataclass(frozen=True)
class Skip:
    where: str
    repo: str | None
    error: str


@dataclass(frozen=True)
class Catalog:
    bundles: tuple[Bundle, ...]
    store: dict
    duplicates: tuple[str, ...]
    skipped: tuple[Skip, ...]


def version_key(v):
    """Sort key for version strings and tags ('20.12.46', '1.48.0-dev.3', 'v2.0'): one leading v/V is dropped; a part
    of digits is its int, any other part is -1; a trailing 0 makes a release outrank its prerelease ('1.48.0' >
    '1.48.0-dev.3', 'v1.0' > 'v1.0-rc1')."""
    v = v[1:] if v[:1] in ("v", "V") else v
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.\-+_]", v)) + (0,)


def _newest_first(versions):
    return tuple(sorted(set(versions), key=version_key, reverse=True))


# ---- parsing (catalog part §1.3, spec §2.1) ----

def _need(ok, path, expected, got):
    if not ok:
        raise CatalogInvalid(f"{path}: expected {expected}, got {got!r}"[:300])


def _text(v, path, optional=False):
    if optional and v is None:
        return None
    _need(isinstance(v, str) and (optional or v.strip()), path, "a string" if optional else "a non-empty string", v)
    return v


def _package(v, path):
    _need(isinstance(v, str) and server.PACKAGE.match(v), path, "a package name", v)
    return v


def _compat_entry(e, path):
    """One compatibility entry (three dialects) -> {package: [versions]}."""
    out = {}
    if isinstance(e, dict):
        for pkg, vs in e.items():
            _package(pkg, f"{path}.{pkg}")
            _need(isinstance(vs, list) and all(isinstance(x, str) for x in vs), f"{path}.{pkg}", "a list of versions", vs)
            out.setdefault(pkg, []).extend(vs)
        return out
    _need(isinstance(e, list), path, "a list or an object", type(e).__name__)
    for i, x in enumerate(e):
        p = f"{path}[{i}]"
        _need(isinstance(x, dict), p, "an object", x)
        out.setdefault(_package(x.get("packageName"), f"{p}.packageName"), []).extend(_targets(x, p))
    return out


def _targets(x, path):
    """Versions of one {packageName, targets|versions} record; a target without version adds none."""
    if "versions" in x:
        vs = x["versions"]
        _need(isinstance(vs, list) and all(isinstance(v, str) for v in vs), f"{path}.versions", "a list of strings", vs)
        return vs
    ts = x.get("targets")
    _need(ts is None or isinstance(ts, list), f"{path}.targets", "a list or null", ts)
    out = []
    for j, t in enumerate(ts or ()):
        _need(isinstance(t, dict), f"{path}.targets[{j}]", "an object", t)
        _need(t.get("version") is None or isinstance(t["version"], str), f"{path}.targets[{j}].version",
              "a string or nothing", t.get("version"))
        if t.get("version") is not None:
            out.append(t["version"])
    return out


def _patch(q, path, compat):
    _need(isinstance(q, dict), path, "an object", q)
    name = _text(q.get("name"), f"{path}.name")
    desc = _text(q.get("description"), f"{path}.description", optional=True)
    dflt = q.get("default", q.get("use"))
    _need("default" not in q or "use" not in q or q["default"] == q["use"], path, "equal default and use",
          (q.get("default"), q.get("use")))
    _need(dflt is None or isinstance(dflt, bool), f"{path}.default", "true, false or nothing", dflt)
    _need(not ("compatiblePackagesKey" in q and "compatibilityKey" in q), path,
          "one of compatiblePackagesKey/compatibilityKey", "both")
    key = q.get("compatiblePackagesKey", q.get("compatibilityKey"))
    if key is None:
        return Patch(name, desc, dflt, ()), {}
    _need(server.is_int(key) and 0 <= key < len(compat), f"{path}.compatiblePackagesKey",
          f"an index < {len(compat)}", key)
    entry = compat[key]
    if isinstance(entry, CatalogInvalid):
        raise entry
    return Patch(name, desc, dflt, tuple(entry)), entry


def _bundle(b, path, compat):
    _need(isinstance(b, dict), path, "an object", type(b).__name__)
    host = b.get("source")
    _need(host in ("github", "gitlab"), f"{path}.source", "github or gitlab", host)
    repo = b.get("repo")
    _need(isinstance(repo, str) and (server.REPO if host == "github" else GITLAB_REPO).match(repo), f"{path}.repo",
          "owner/name", repo)
    branch = b.get("branch")
    _need(branch is None or (isinstance(branch, str) and server.SLUG.match(branch)), f"{path}.branch",
          "a branch name or nothing", branch)
    stars, updated = b.get("stars"), b.get("updatedAt")
    _need(server.is_int(stars), f"{path}.stars", "an integer", stars)
    _need(updated is None or server.is_int(updated), f"{path}.updatedAt", "epoch milliseconds or null", updated)
    patches = b.get("patches")
    _need(isinstance(patches, list), f"{path}.patches", "a list", type(patches).__name__)
    apps, out = {}, []
    for i, q in enumerate(patches):
        p, entry = _patch(q, f"{path}.patches[{i}]", compat)
        out.append(p)
        for pkg, vs in entry.items():
            apps.setdefault(pkg, []).extend(vs)
    return Bundle(host, repo, branch, _text(b.get("name"), f"{path}.name"), _text(b.get("author"), f"{path}.author"),
                  _text(b.get("repoDescription"), f"{path}.repoDescription", optional=True) or None, stars,
                  None if updated is None else db.iso(datetime.fromtimestamp(updated / 1000, timezone.utc)),
                  {pkg: _newest_first(vs) for pkg, vs in apps.items()}, tuple(out))


def _store_app(pkg, v, path):
    _package(pkg, path)
    _need(isinstance(v, dict), path, "an object", type(v).__name__)
    icon = v.get("iconUrl")
    _need(icon is None or (isinstance(icon, str) and icon.startswith("https://")), f"{path}.iconUrl",
          "an https URL or nothing", icon)
    desc = _text(v.get("description"), f"{path}.description", optional=True)
    return StoreApp(pkg, _text(v.get("name"), f"{path}.name"), _text(v.get("category"), f"{path}.category"),
                    None if desc is None else html.unescape(desc), icon)


def _json_root(raw):
    try:
        return json.loads(raw)
    except ValueError as e:
        raise CatalogInvalid(f"$: expected JSON, got {e}")


def parse_official(raw):
    """bundles.json -> Catalog. Raises CatalogInvalid only for a bad file; a bad bundle or store value is skipped."""
    d = _json_root(raw)
    _need(isinstance(d, dict), "$", "an object", type(d).__name__)
    for key, typ in (("bundles", list), ("compatibilities", list), ("store", dict)):
        _need(isinstance(d.get(key), typ), key, typ.__name__, type(d.get(key)).__name__)
    compat = []
    for i, e in enumerate(d["compatibilities"]):
        try:
            compat.append(_compat_entry(e, f"compatibilities[{i}]"))
        except CatalogInvalid as err:   # only bundles pointing here are skipped
            compat.append(err)
    seen, bundles, dups, skipped = set(), [], [], []
    for i, b in enumerate(d["bundles"]):
        try:
            x = _bundle(b, f"bundles[{i}]", compat)
        except CatalogInvalid as err:
            repo = b.get("repo") if isinstance(b, dict) and isinstance(b.get("repo"), str) else None
            skipped.append(Skip(f"bundles[{i}]", repo, str(err)))
            continue
        key = (x.host, x.repo, x.branch or "")
        if key in seen:   # first record wins, the repo is listed
            dups.append(x.repo)
            continue
        seen.add(key)
        bundles.append(x)
    store = {}
    for pkg, v in d["store"].items():
        try:
            store[pkg] = _store_app(pkg, v, f"store.{pkg}")
        except CatalogInvalid as err:
            skipped.append(Skip(f"store.{pkg}", None, str(err)))
    return Catalog(tuple(bundles), store, tuple(dups), tuple(skipped))


def parse_patches_list(raw):
    """patches-list.json -> (version, patches, package -> versions, package -> name, skipped). Patch dicts:
    {name, description, default_enabled, packages}; packages [] = universal. A bad patch entry is skipped."""
    d = _json_root(raw)
    _need(isinstance(d, dict), "$", "an object", type(d).__name__)
    _need(isinstance(d.get("version"), str), "version", "a string", d.get("version"))
    _need(isinstance(d.get("patches"), list), "patches", "a list", type(d.get("patches")).__name__)
    patches, apps, names, skipped = [], {}, {}, []
    for i, q in enumerate(d["patches"]):
        path = f"patches[{i}]"
        try:
            _need(isinstance(q, dict), path, "an object", q)
            _need(isinstance(q.get("default"), bool), f"{path}.default", "true or false", q.get("default"))
            cps = q.get("compatiblePackages")
            _need(cps is None or isinstance(cps, list), f"{path}.compatiblePackages", "a list or null", cps)
            pkgs = {}
            for j, cp in enumerate(cps or ()):
                p = f"{path}.compatiblePackages[{j}]"
                _need(isinstance(cp, dict), p, "an object", cp)
                pkg = _package(cp.get("packageName"), f"{p}.packageName")
                pkgs[pkg] = (_targets(cp, p), _text(cp.get("name"), f"{p}.name", optional=True))
            p = {"name": _text(q.get("name"), f"{path}.name"),
                 "description": _text(q.get("description"), f"{path}.description", optional=True),
                 "default_enabled": q["default"], "packages": sorted(pkgs)}
        except CatalogInvalid as err:
            skipped.append(Skip(path, None, str(err)))
            continue
        patches.append(p)
        for pkg, (vs, name) in pkgs.items():
            apps.setdefault(pkg, []).extend(vs)
            if name:
                names.setdefault(pkg, name)
    return d["version"], patches, {k: list(_newest_first(v)) for k, v in apps.items()}, names, skipped


# ---- refresh and apply (catalog part §1.2, §1.4, §3.3) ----

FEEDS = (("official", "https://morphe-patches.software/data/bundles.json", parse_official),)
FEEDS_ORIGINS = frozenset(f[0] for f in FEEDS)
_refresh_lock = threading.Lock()


def _feed_path(feed):
    return db.DATA / "catalog" / f"{feed}.json"


def _skip_dicts(skipped):
    return [{"where": s.where, "repo": s.repo, "error": s.error} for s in skipped]


def _fetch_feed(feed, url, parse, log):
    """True when a new copy was stored. Raises on any failure; the last good copy stays."""
    row = db.conn().execute("SELECT etag FROM catalog_feed WHERE feed = ?", (feed,)).fetchone()
    path = _feed_path(feed)
    headers = {"User-Agent": "Relume"}   # public data: no GitHub token
    if row and row["etag"] and path.is_file():
        headers["If-None-Match"] = row["etag"]
    st, hdr, body, _ = upstream.http_get(url, headers, timeout=FEED_TIMEOUT, max_bytes=FEED_MAX_BYTES)
    now = db.utcnow()
    if st == 304:
        with db.tx() as c:
            c.execute("UPDATE catalog_feed SET last_attempt_at = ? WHERE feed = ?", (now, feed))
        log.line(f"[+] catalog {feed}: not modified")
        return False
    if st != 200:
        raise ValueError(f"{url}: expected HTTP 200 or 304, got {st}")
    cat = parse(body)
    part = path.with_name(path.name + ".part")
    part.write_bytes(body)
    os.replace(part, path)
    for s in cat.skipped:
        log.line(f"[!] catalog {feed}: skipped {s.where}{' (' + s.repo + ')' if s.repo else ''}: {s.error}")
    with db.tx() as c:   # the ETag is stored only together with a good copy
        c.execute("""INSERT INTO catalog_feed(feed, etag, last_attempt_at, last_ok_at, last_error, duplicates, skipped)
            VALUES (?, ?, ?, ?, NULL, ?, ?) ON CONFLICT(feed) DO UPDATE SET etag = excluded.etag,
            last_attempt_at = excluded.last_attempt_at, last_ok_at = excluded.last_ok_at, last_error = NULL,
            duplicates = excluded.duplicates, skipped = excluded.skipped""",
                  (feed, hdr.get("etag"), now, now, json.dumps(list(cat.duplicates)),
                   json.dumps(_skip_dicts(cat.skipped))))
    log.line(f"[+] catalog {feed}: {len(cat.bundles)} bundles, {len(cat.skipped)} skipped")
    return True


def _feed_failed(feed, error, log):
    with db.tx() as c:
        c.execute("""INSERT INTO catalog_feed(feed, last_attempt_at, last_error) VALUES (?, ?, ?)
            ON CONFLICT(feed) DO UPDATE SET last_attempt_at = excluded.last_attempt_at,
            last_error = excluded.last_error""", (feed, db.utcnow(), error))
    log.line(f"[!] catalog {feed}: {error}")


def _write_rows(c, bundle_id, apps, patches):
    """Replace a bundle's browse rows. patches: (package or UNIVERSAL, name, description, default_enabled)."""
    c.execute("DELETE FROM bundle_app WHERE bundle_id = ?", (bundle_id,))
    c.execute("DELETE FROM bundle_patch WHERE bundle_id = ?", (bundle_id,))
    c.executemany("INSERT INTO bundle_app(bundle_id, package, versions) VALUES (?, ?, ?)",
                  [(bundle_id, pkg, json.dumps(list(vs))) for pkg, vs in apps.items()])
    # ponytail: a second patch with the same name for the same package is dropped (first wins)
    c.executemany("INSERT OR IGNORE INTO bundle_patch(bundle_id, package, name, description, default_enabled) "
                  "VALUES (?, ?, ?, ?, ?)", [(bundle_id, *p[:3], None if p[3] is None else int(p[3])) for p in patches])
    c.executemany("INSERT OR IGNORE INTO store_app(package, name) VALUES (?, ?)", [(p, p) for p in apps])


def _bundle_used(c, bundle_id):
    return [r[0] for r in c.execute("SELECT id FROM item WHERE bundle_id = ? ORDER BY id", (bundle_id,))]


def _apply(log):
    """Merge the last good copy of every feed by (host, repo, branch) in FEED_RANK order and write it in one tx."""
    merged, store = {}, {}
    for feed, _, parse in sorted(FEEDS, key=lambda f: FEED_RANK[f[0]]):
        path = _feed_path(feed)
        if not path.is_file():
            continue
        cat = parse(path.read_bytes())
        for b in cat.bundles:
            merged.setdefault((b.host, b.repo, b.branch or ""), (feed, b))
        for pkg, s in cat.store.items():
            store.setdefault(pkg, s)
    with db.tx() as c:
        rows = {(r["host"], r["repo"], r["branch"] or ""): r for r in c.execute("SELECT * FROM bundle")}
        for key, (origin, b) in merged.items():
            r = rows.get(key)
            if r is not None and r["origin"] not in FEEDS_ORIGINS:
                continue   # a built-in or custom bundle owns the repo
            cols = (origin, b.name, b.author, b.description, b.stars, b.updated_at)
            if r is None:
                bid = c.execute("""INSERT INTO bundle(origin, name, author, description, stars, updated_at, host, repo,
                    branch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (*cols, b.host, b.repo, b.branch)).lastrowid
            else:
                bid = r["id"]
                c.execute("""UPDATE bundle SET origin = ?, name = ?, author = ?, description = ?, stars = ?,
                    updated_at = ?, listed = 1 WHERE id = ?""", (*cols, bid))
            _write_rows(c, bid, b.apps, [(pkg or UNIVERSAL, p.name, p.description, p.default_enabled)
                                         for p in b.patches for pkg in (p.packages or (None,))])
        gone = [r for key, r in rows.items() if r["origin"] in FEEDS_ORIGINS and key not in merged]
        for r in gone:
            if _bundle_used(c, r["id"]):
                c.execute("UPDATE bundle SET listed = 0 WHERE id = ?", (r["id"],))   # builds continue from the repo
            else:
                c.execute("DELETE FROM bundle WHERE id = ?", (r["id"],))
        for s in store.values():
            c.execute("""INSERT INTO store_app(package, name, category, description, icon_url) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(package) DO UPDATE SET name = excluded.name, category = excluded.category,
                description = excluded.description, icon_url = excluded.icon_url,
                icon_file = CASE WHEN icon_url IS excluded.icon_url THEN icon_file END,
                icon_tried_url = CASE WHEN icon_url IS excluded.icon_url THEN icon_tried_url END""",
                      (s.package, s.name, s.category, s.description, s.icon_url))
    log.line(f"[+] catalog applied: {len(merged)} bundles, {len(gone)} left the feeds")


def _get_list(repo, tag):
    """Raw patches-list.json of a release tag."""
    url = LIST_URL.format(repo, tag)
    st, _, body, _ = upstream.http_get(url, {"User-Agent": "Relume"}, timeout=FEED_TIMEOUT, max_bytes=FEED_MAX_BYTES)
    if st == 404:
        raise CatalogInvalid(f"{repo} has no patches-list.json at {tag}")
    if st != 200:
        raise ValueError(f"{url}: expected HTTP 200, got {st}")
    return body


def _store_list(c, bundle_id, tag, parsed):
    _, patches, apps, names, skipped = parsed
    _write_rows(c, bundle_id, apps, [(pkg, p["name"], p["description"], p["default_enabled"])
                                     for p in patches for pkg in (p["packages"] or [UNIVERSAL])])
    for pkg, name in names.items():   # catalog store rows (category set) win
        c.execute("UPDATE store_app SET name = ? WHERE package = ? AND category IS NULL", (name, pkg))
    err = "skipped: " + "; ".join(s.error for s in skipped) if skipped else None
    c.execute("UPDATE bundle SET list_tag = ?, list_error = ? WHERE id = ?", (tag, err, bundle_id))


def _refresh_list(b, log):
    """Built-in / custom Morphe bundle: re-read patches-list.json when the latest release tag changed."""
    try:
        row = upstream.check(b["upstream_id"])
        if row["check_error"]:
            raise ValueError(f"release check: {row['check_error']}")
        tag = row["latest_tag"]
        if tag == b["list_tag"]:
            return
        parsed = parse_patches_list(_get_list(b["repo"], tag))
    except (OSError, ValueError, upstream.TokenRejected, upstream.RateLimited) as e:
        with db.tx() as c:
            c.execute("UPDATE bundle SET list_error = ? WHERE id = ?", (str(e), b["id"]))
        log.line(f"[!] patch list {b['repo']}: {e}")
        raise
    with db.tx() as c:
        _store_list(c, b["id"], tag, parsed)
    log.line(f"[+] patch list {b['repo']} {tag}: {len(parsed[1])} patches, {len(parsed[4])} skipped")


def refresh(log):
    """Fetch every feed, apply what succeeded, refresh built-in/custom lists; then raise CatalogRefreshFailed when
    anything failed. A concurrent caller gets CatalogRefreshBusy."""
    if not _refresh_lock.acquire(blocking=False):
        raise CatalogRefreshBusy("catalog refresh already running")
    try:
        errors, fetched = [], False
        for feed, url, parse in FEEDS:
            try:
                fetched = _fetch_feed(feed, url, parse, log) or fetched
            except (OSError, ValueError) as e:   # CatalogInvalid is a ValueError
                _feed_failed(feed, str(e), log)
                errors.append(f"{feed}: {e}")
        empty = not db.conn().execute(f"SELECT 1 FROM bundle WHERE origin IN ({','.join('?' * len(FEEDS_ORIGINS))})",
                                      tuple(FEEDS_ORIGINS)).fetchone()
        if fetched or empty:
            _apply(log)
        for b in db.conn().execute("""SELECT * FROM bundle WHERE origin IN ('builtin', 'custom') AND engine = 'morphe'
                AND upstream_id IS NOT NULL ORDER BY id""").fetchall():
            try:
                _refresh_list(b, log)
            except (OSError, ValueError, upstream.TokenRejected, upstream.RateLimited) as e:
                errors.append(f"{b['repo']}: {e}")
        if errors:
            raise CatalogRefreshFailed("; ".join(errors))
    finally:
        _refresh_lock.release()


# ---- read API (spec §2.5) ----

def feed_status():
    c = db.conn()
    feeds = []
    for feed, _, _ in FEEDS:
        r = c.execute("SELECT * FROM catalog_feed WHERE feed = ?", (feed,)).fetchone()
        n = c.execute("""SELECT COUNT(DISTINCT b.id), COUNT(DISTINCT a.package) FROM bundle b
            LEFT JOIN bundle_app a ON a.bundle_id = b.id WHERE b.origin = ? AND b.listed = 1""", (feed,)).fetchone()
        feeds.append({"feed": feed, "origin": feed, "last_ok_at": r and r["last_ok_at"],
                      "last_attempt_at": r and r["last_attempt_at"], "last_error": r and r["last_error"],
                      "bundle_count": n[0], "app_count": n[1],
                      "duplicates": json.loads(r["duplicates"]) if r and r["duplicates"] else [],
                      "skipped": json.loads(r["skipped"]) if r and r["skipped"] else []})
    sources = [{"bundle_id": r["id"], "origin": r["origin"], "repo": r["repo"], "list_tag": r["list_tag"],
                "last_error": r["list_error"]} for r in c.execute(
        "SELECT * FROM bundle WHERE origin IN ('builtin', 'custom') ORDER BY origin, id")]
    return {"feeds": feeds, "sources": sources}


def _origins(seen):
    return sorted(seen, key=FEED_RANK.__getitem__)


def market_apps():
    """Every package a listed bundle targets, with metadata aggregated over those bundles."""
    apps = {}
    for r in db.conn().execute("""SELECT a.package, a.versions, b.origin, b.stars, b.updated_at, s.name, s.category,
            s.description FROM bundle_app a JOIN bundle b ON b.id = a.bundle_id
            JOIN store_app s ON s.package = a.package WHERE b.listed = 1"""):
        o = apps.setdefault(r["package"], {"package": r["package"], "name": r["name"], "category": r["category"],
                                           "description": r["description"], "origins": set(), "bundle_count": 0,
                                           "stars": None, "versions": set(), "updated_at": None})
        o["origins"].add(r["origin"])
        o["bundle_count"] += 1
        o["versions"].update(json.loads(r["versions"]))
        if r["stars"] is not None and (o["stars"] is None or r["stars"] > o["stars"]):
            o["stars"] = r["stars"]
        if r["updated_at"] and (o["updated_at"] is None or r["updated_at"] > o["updated_at"]):
            o["updated_at"] = r["updated_at"]
    out = []
    for o in apps.values():
        vs = o.pop("versions")
        out.append(dict(o, origins=_origins(o["origins"]),
                        newest_version=max(vs, key=version_key) if vs else None))
    return sorted(out, key=lambda o: (o["name"].lower(), o["package"]))


def app_bundles(package):
    """Every bundle that targets the package (listed or kept for an app). KeyError for an unknown package."""
    c = db.conn()
    rows = c.execute("""SELECT b.*, a.versions FROM bundle_app a JOIN bundle b ON b.id = a.bundle_id
        WHERE a.package = ?""", (package,)).fetchall()
    if not rows:
        raise KeyError(package)
    out = []
    for r in sorted(rows, key=lambda r: (FEED_RANK[r["origin"]], -(r["stars"] or 0), r["repo"])):
        patches = [{"name": p["name"], "description": p["description"],
                    "default_enabled": None if p["default_enabled"] is None else bool(p["default_enabled"])}
                   for p in c.execute("""SELECT name, description, default_enabled FROM bundle_patch
                       WHERE bundle_id = ? AND package IN (?, ?) ORDER BY name""", (r["id"], package, UNIVERSAL))]
        selectable = r["host"] == "github"
        out.append({"bundle_id": r["id"], "origin": r["origin"], "host": r["host"], "repo": r["repo"],
                    "branch": r["branch"], "name": r["name"], "author": r["author"], "description": r["description"],
                    "stars": r["stars"], "updated_at": r["updated_at"], "engine": r["engine"],
                    "listed": bool(r["listed"]), "selectable": selectable,
                    "not_selectable_reason": None if selectable else GITLAB_REASON, "list_tag": r["list_tag"],
                    "versions": json.loads(r["versions"]), "patches": patches})
    return out


# ---- bundle -> upstream (catalog part §4) ----

def upstream_row(c, kind, repo, channel, tag, asset_regex):
    r = c.execute("SELECT id, kind FROM upstream WHERE repo = ? AND channel = ? AND ifnull(tag, '') = ifnull(?, '')",
                  (repo, channel, tag)).fetchone()
    if r is not None:
        if r["kind"] != kind:
            raise server.HttpError(400, "upstream kind conflict")
        if asset_regex is not None:  # one regex per shared upstream row: an edit must not be silently dropped
            c.execute("UPDATE upstream SET asset_regex = ? WHERE id = ?", (asset_regex, r["id"]))
        return r["id"]
    return c.execute("INSERT INTO upstream(kind, repo, channel, tag, asset_regex) VALUES (?, ?, ?, ?, ?)",
                     (kind, repo, channel, tag, asset_regex)).lastrowid


def _bundle_row(bundle_id):
    r = db.conn().execute("SELECT * FROM bundle WHERE id = ?", (bundle_id,)).fetchone()
    if r is None:
        raise server.HttpError(404, f"bundle {bundle_id} not found")
    return r


def engine_cli(engine):
    """Upstream id of the engine's CLI row; the row is created on first use (the migration keeps the live ones)."""
    repo, channel, tag, rx = ENGINE_CLI[engine]
    r = db.conn().execute("SELECT id FROM upstream WHERE repo = ? AND channel = ? AND ifnull(tag, '') = ifnull(?, '')",
                          (repo, channel, tag)).fetchone()
    if r is not None:
        return r["id"]
    with db.tx() as c:
        return upstream_row(c, "cli", repo, channel, tag, rx)


def ensure_upstream(bundle_id):
    """Release tracker of a bundle, created on first enable. A catalog branch maps to the prerelease channel."""
    b = _bundle_row(bundle_id)
    if b["host"] != "github":
        raise server.HttpError(409, f"bundle {bundle_id} ({b['host']}:{b['repo']}): {GITLAB_REASON}",
                               code="bundle_not_selectable")
    if b["upstream_id"] is not None:
        return b["upstream_id"]
    with db.tx() as c:
        uid = upstream_row(c, ENGINES[b["engine"]]["kind"], b["repo"], "prerelease" if b["branch"] else "stable",
                           None, b["asset_regex"])
        c.execute("UPDATE bundle SET upstream_id = ? WHERE id = ?", (uid, bundle_id))
    return uid


def patch_list(bundle_id, package):
    """(downloaded tag, file names, bundle_info patches or None) of the bundle's release for one package."""
    uid = _bundle_row(bundle_id)["upstream_id"]
    tag = None if uid is None else db.conn().execute(
        "SELECT downloaded_tag FROM upstream WHERE id = ?", (uid,)).fetchone()[0]
    if not tag:
        return None, [], None
    d = upstream.tag_dir(uid, tag)
    files = sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []
    b = db.conn().execute("SELECT patches FROM bundle_info WHERE upstream_id = ? AND tag = ? AND package = ?",
                          (uid, tag, package)).fetchone()
    return tag, files, (json.loads(b[0]) if b else None)


# ---- custom sources (catalog part §3.2, spec §2.3) ----

def list_custom():
    c = db.conn()
    return [{"bundle_id": r["id"], "repo": r["repo"], "name": r["name"], "engine": r["engine"],
             "asset_regex": r["asset_regex"], "list_tag": r["list_tag"], "list_error": r["list_error"],
             "packages": [x[0] for x in c.execute("SELECT package FROM bundle_app WHERE bundle_id = ? ORDER BY package",
                                                  (r["id"],))]}
            for r in c.execute("SELECT * FROM bundle WHERE origin = 'custom' ORDER BY id")]


def _module_fields(asset_regex, packages):
    if not isinstance(asset_regex, str) or not asset_regex:
        raise server.HttpError(400, f"asset_regex: expected a regular expression for module sources, got {asset_regex!r}")
    try:
        re.compile(asset_regex)
    except re.error as e:
        raise server.HttpError(400, f"asset_regex: expected a regular expression, got {asset_regex!r} ({e})")
    if (not isinstance(packages, (list, tuple)) or not 1 <= len(packages) <= MAX_MODULE_PACKAGES
            or len(set(packages)) != len(packages)):
        raise server.HttpError(400, f"packages: expected 1-{MAX_MODULE_PACKAGES} distinct packages, got {packages!r}")
    return [server.validate_package(p, "packages") for p in packages]


def add_custom(url, engine, asset_regex=None, packages=()):
    """New custom bundle (GitHub only). Morphe: its release must carry patches-list.json; module: regex + packages.
    A repo already listed by a feed is converted in place, so an enabled app keeps its bundle."""
    if engine not in ENGINES:
        raise server.HttpError(400, f"engine: expected one of {', '.join(ENGINES)}, got {engine!r}")
    repo = server.validate_repo(url, "url")
    module = ENGINES[engine]["kind"] == "module"
    if module:
        packages = _module_fields(asset_regex, packages)
    elif asset_regex is not None or packages:
        raise server.HttpError(400, f"asset_regex and packages are only for module engines, got engine {engine}")
    r = db.conn().execute("SELECT * FROM bundle WHERE host = 'github' AND repo = ? AND branch IS NULL",
                          (repo,)).fetchone()
    if r is not None and r["origin"] not in FEEDS_ORIGINS:
        raise server.HttpError(409, f"{repo} is already a {r['origin']} source")
    if r is not None and r["engine"] != engine:
        raise server.HttpError(409, f"{repo} is a catalog {r['engine']} bundle, got engine {engine}")
    parsed = tag = None
    if not module:   # fetch before writing: a refused repo leaves nothing stored
        try:
            rel, _ = upstream.fetch({"kind": "patches", "repo": repo, "channel": "stable", "tag": None,
                                     "asset_regex": None})
        except (upstream.CheckError, upstream.TokenRejected, upstream.RateLimited) as e:
            raise server.HttpError(400, f"{repo}: release check: {e}")
        tag = rel["tag_name"]
        try:
            raw = _get_list(repo, tag)
        except CatalogInvalid as e:   # "<repo> has no patches-list.json at <tag>"
            raise server.HttpError(400, str(e))
        except (OSError, ValueError) as e:
            raise server.HttpError(400, f"{repo}: {e}")
        try:
            parsed = parse_patches_list(raw)
        except CatalogInvalid as e:
            raise server.HttpError(400, f"{repo} patches-list.json at {tag}: {e}")
    with db.tx() as c:
        uid = upstream_row(c, ENGINES[engine]["kind"], repo, "stable", None, asset_regex)
        if r is None:
            bid = c.execute("""INSERT INTO bundle(origin, host, repo, engine, name, asset_regex, upstream_id)
                VALUES ('custom', 'github', ?, ?, ?, ?, ?)""", (repo, engine, repo, asset_regex, uid)).lastrowid
        else:
            bid = r["id"]
            c.execute("UPDATE bundle SET origin = 'custom', listed = 1, upstream_id = ? WHERE id = ?", (uid, bid))
        if module:
            _write_rows(c, bid, {p: () for p in packages}, [])
        else:
            _store_list(c, bid, tag, parsed)
    return bid


def delete_custom(bundle_id):
    r = _bundle_row(bundle_id)
    if r["origin"] != "custom":
        raise server.HttpError(404, f"bundle {bundle_id}: expected a custom source, got origin {r['origin']}")
    uid = r["upstream_id"]
    with db.tx() as c:
        used = _bundle_used(c, bundle_id)
        if used:
            raise server.HttpError(409, f"custom source {r['repo']} is used by apps {used}", code="source_in_use",
                                   item_ids=used)
        c.execute("DELETE FROM bundle WHERE id = ?", (bundle_id,))
        keep = uid is None or c.execute("""SELECT EXISTS(SELECT 1 FROM bundle WHERE upstream_id = ?)
            OR EXISTS(SELECT 1 FROM project WHERE upstream_id = ?)""", (uid, uid)).fetchone()[0]
        if not keep:
            c.execute("DELETE FROM upstream WHERE id = ?", (uid,))
    if not keep:
        shutil.rmtree(db.DATA / "upstream" / str(uid), ignore_errors=True)


# ---- enabled-app validation helpers (used by apps.py) ----

URL_RE = re.compile(r"^https://[^\s\"'<>]+$")
ARCH_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9 ._+-]*)?$")   # no leading . or - (grep option); seed has 'arm64-v8a + armeabi-v7a' (icysymmetra.sh)
MINVER_RE = re.compile(r"^[A-Za-z0-9 .+-]*$")
DOWNLOAD_DEFAULT = {"kind": "apkmirror", "type": "apk", "arch": None, "dpi": None, "minver": None, "url": None}


def _name(v, field="name"):
    if not isinstance(v, str) or not v.strip() or len(v) > 80:
        raise server.HttpError(400, f"invalid {field}")
    return v.strip()


def display_name(v):
    return None if v is None else _name(v, "display_name")


def _opt(v, rx, field):
    if v is None:
        return None
    if not isinstance(v, str) or not rx.match(v):
        raise server.HttpError(400, f"invalid {field}")
    return v


def default_variants():
    return [
        {"name": "universal", "abi": "universal", "download": dict(DOWNLOAD_DEFAULT), "exclude_splits": None, "strip_libs": None},
        {"name": "arm64-v8a", "abi": "arm64-v8a", "download": dict(DOWNLOAD_DEFAULT), "exclude_splits": None, "strip_libs": "arm64-v8a"},
    ]


def validate_variants(variants):
    if not isinstance(variants, list) or not 1 <= len(variants) <= 10:
        raise server.HttpError(400, "invalid variants")
    out, names = [], set()
    for v in variants:
        if not isinstance(v, dict):
            raise server.HttpError(400, "invalid variant")
        name = server.validate_slug(v.get("name"), "variant name")
        if name in names:
            raise server.HttpError(400, "duplicate variant name")
        names.add(name)
        if v.get("abi") not in ABIS:
            raise server.HttpError(400, "invalid abi")
        d = v.get("download")
        if (not isinstance(d, dict) or d.get("kind") not in ("apkmirror", "apkpure", "googleplay", "url")
                or d.get("type", "apk") not in ("apk", "bundle", "bundle_extract")):
            raise server.HttpError(400, "invalid download")
        url = _opt(d.get("url"), URL_RE, "download url")
        if (d["kind"] == "url") != (url is not None):
            raise server.HttpError(400, "download url is required for kind url and only for it")
        dl = {"kind": d["kind"], "type": d.get("type", "apk"),
              "arch": _opt(d.get("arch"), ARCH_RE, "arch"),
              "dpi": None if d.get("dpi") is None else server.validate_slug(d["dpi"], "dpi"),
              "minver": _opt(d.get("minver"), MINVER_RE, "minver"), "url": url}
        ex = v.get("exclude_splits")
        if ex is not None:
            if not isinstance(ex, list):
                raise server.HttpError(400, "invalid exclude_splits")
            ex = [server.validate_slug(x, "exclude_splits") for x in ex]
        sl = v.get("strip_libs")
        if sl is not None and sl not in ABIS:
            raise server.HttpError(400, "invalid strip_libs")
        out.append({"name": name, "abi": v["abi"], "download": dl, "exclude_splits": ex, "strip_libs": sl})
    return out


def is_module(engine):
    return ENGINES.get(engine, {}).get('kind') == 'module'


def validate_engine_options(engine, value):
    if not is_module(engine):
        if value is not None:
            raise server.HttpError(400, 'engine_options must be null for patches items')
        return None
    if not isinstance(value, dict) or set(value) != {'sigbypass', 'injectdex'}:
        raise server.HttpError(400, 'engine_options needs exactly sigbypass and injectdex')
    sb, inj = value['sigbypass'], value['injectdex']
    if not server.is_int(sb) or not 0 <= sb <= ENGINES[engine]['max_sigbypass']:
        raise server.HttpError(400, f'sigbypass must be 0-{ENGINES[engine]['max_sigbypass']} for {engine}')
    if not isinstance(inj, bool):
        raise server.HttpError(400, 'injectdex must be true or false')
    return {'sigbypass': sb, 'injectdex': inj}


def check_xposed_variants(variants):
    for v in variants if isinstance(variants, list) else []:
        if not isinstance(v, dict):
            continue  # shape errors are answered by the variant validation
        if v.get('exclude_splits') is not None or v.get('strip_libs') is not None:
            raise server.HttpError(400, 'exclude_splits and strip_libs must be null for module items')
        if (v.get('download') or {}).get('type') not in ('apk', 'bundle'):
            raise server.HttpError(400, 'download.type must be apk or bundle for module items')


def set_apkmirror_versions(package, versions):
    with db.tx() as c:
        c.execute("UPDATE app SET apkmirror_versions = ?, apkmirror_checked_at = ? WHERE package = ?",
                  (json.dumps(list(versions)), db.utcnow(), package))


# ---- icons (catalog part §5, spec §2.6) ----

class _IconFailed(Exception):
    def __init__(self, url, reason):
        super().__init__(f"{url}: {reason}")


def _icon_url_ok(url, hosts):
    s = urlsplit(url)
    return s.scheme == "https" and s.hostname in hosts


def _icon_get(url, hosts, max_bytes):
    """(headers, body) of a 200 answer whose request and final URL are https on an allowed host."""
    if not _icon_url_ok(url, hosts):
        raise ValueError(f"expected https on {', '.join(sorted(hosts))}, got {url}")
    st, hdr, body, final = upstream.http_get(url, {"User-Agent": "Mozilla/5.0"}, timeout=ICON_TIMEOUT,
                                             max_bytes=max_bytes)
    if not _icon_url_ok(final, hosts):
        raise ValueError(f"redirect left the allowed hosts: expected https on {', '.join(sorted(hosts))}, got {final}")
    if st != 200:
        raise ValueError(f"expected HTTP 200, got {st}")
    return hdr, body


def _fetch_icon(package, icon_url):
    """Icon file name in DATA/icons; the catalog URL, else the Play page og:image. Raises (url, reason)."""
    url = icon_url
    if url is None:
        page = PLAY_URL.format(package)
        try:
            _, body = _icon_get(page, {PLAY_HOST}, PLAY_PAGE_MAX_BYTES)
        except (OSError, ValueError) as e:
            raise _IconFailed(page, e)
        m = OG_IMAGE_RE.search(body.decode("utf-8", "replace"))
        if m is None:
            raise _IconFailed(page, "expected an og:image meta tag, got none")
        url = SIZE_SUFFIX_RE.sub(PLAY_ICON_SIZE, html.unescape(m[1]))
    try:
        hdr, data = _icon_get(url, ICON_HOSTS, ICON_MAX_BYTES)
    except (OSError, ValueError) as e:
        raise _IconFailed(url, e)
    ctype = hdr.get("content-type", "").split(";")[0].strip()
    if ctype not in ICON_EXT or not data:
        raise _IconFailed(url, f"expected a non-empty {'/'.join(ICON_EXT)} image, got {ctype or 'no type'} "
                               f"({len(data)} bytes)")
    d = db.DATA / "icons"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{package}.{ICON_EXT[ctype]}"
    (d / name).write_bytes(data)
    return name


def get_icon(req):
    """Cached icon, fetched lazily on a miss. A failure is logged, remembered per source URL and answered 404."""
    pkg = server.validate_package(req.params["pkg"], "package")
    r = db.conn().execute("SELECT icon_url, icon_file, icon_tried_url FROM store_app WHERE package = ?",
                          (pkg,)).fetchone()
    if r is None:
        raise server.HttpError(404, f"no icon: {pkg} is in no catalog")
    path = db.DATA / "icons" / r["icon_file"] if r["icon_file"] else None
    if path is None or not path.is_file():
        source = r["icon_url"] or PLAY_URL.format(pkg)
        if r["icon_tried_url"] == source:
            raise server.HttpError(404, f"no icon: {source} failed before")
        try:
            name = _fetch_icon(pkg, r["icon_url"])
        except _IconFailed as e:
            print(f"icon {pkg}: {e}", flush=True)
            with db.tx() as c:
                c.execute("UPDATE store_app SET icon_tried_url = ? WHERE package = ?", (source, pkg))
            raise server.HttpError(404, f"no icon: {e}")
        with db.tx() as c:
            c.execute("UPDATE store_app SET icon_file = ?, icon_tried_url = NULL WHERE package = ?", (name, pkg))
        path = db.DATA / "icons" / name
    return server.FileResponse(str(path), cache_seconds=86400)


def post_icon_refresh(req):
    pkg = server.validate_package(req.params["pkg"], "package")
    with db.tx() as c:
        if not c.execute("UPDATE store_app SET icon_file = NULL, icon_tried_url = NULL WHERE package = ?",
                         (pkg,)).rowcount:
            raise server.HttpError(404, f"{pkg} is in no catalog")
    return 200, {"ok": True}


ROUTES = [
    ("GET", "/api/apps/{pkg}/icon", get_icon),
    ("POST", "/api/apps/{pkg}/icon/refresh", post_icon_refresh),
]
