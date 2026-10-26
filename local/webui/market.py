"""Marketplace HTTP API (spec marketplace §6): every new route, on top of apps, catalog, apklocate and build.
No route here is session-only: the Android app reaches all of them with its device token."""
from . import apklocate, apps, build, catalog, db
from .server import HttpError, is_int, validate_package

SORTS = ("name", "bundles", "stars", "updated")
ORIGINS = ("official", "builtin", "custom")
BOOLS = {"true": True, "false": False}
PER_PAGE_MAX, PER_PAGE_DEFAULT = 100, 50
ENABLE_KEYS = ("package", "bundle_id", "patches", "download")
SOURCE_KEYS = ("url", "engine", "asset_regex", "packages")


def _choice(q, key, allowed, default):
    v = q.get(key, default)
    if v is not None and v not in allowed:
        raise HttpError(400, f"marketplace: {key} expected {'|'.join(allowed)}, got {v!r}")
    return v


def _int(q, key, lo, hi, default):
    v = q.get(key)
    if v is None:
        return default
    if not (v.isascii() and v.isdigit()) or not lo <= int(v) <= (hi or int(v)):
        raise HttpError(400, f"marketplace: {key} expected {f'{lo}-{hi}' if hi else f'an integer >= {lo}'}, got {v!r}")
    return int(v)


def _items():
    """package -> (item id, enabled) for every enabled-app row, also the disabled ones."""
    return {r["package"]: (r["id"], bool(r["enabled"])) for r in db.conn().execute("SELECT id, package, enabled FROM item")}


def _icon(package):
    return f"/api/apps/{package}/icon"


def _id(req, what="app"):
    v = req.params["id"]
    if not is_int(v):
        raise HttpError(400, f"{what} id expected an integer, got {v!r}")
    return v


def _with_download(app):
    return {**app, "download": apklocate.download_state(app["package"])}


def _keys(body, allowed, what):
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise HttpError(400, f"{what}: expected keys {', '.join(allowed)}, got {', '.join(unknown)}")


def list_marketplace(req):
    q = req.query
    sort = _choice(q, "sort", SORTS, "name")
    origin = _choice(q, "origin", ORIGINS, None)
    enabled = BOOLS[_choice(q, "enabled", tuple(BOOLS), None)] if "enabled" in q else None
    page, per_page = _int(q, "page", 1, None, 1), _int(q, "per_page", 1, PER_PAGE_MAX, PER_PAGE_DEFAULT)
    text, category = (q.get("q") or "").casefold(), q.get("category")
    items = _items()
    # ponytail: filter and sort in memory over ~1200 apps, move to SQL past ~10k
    rows = [a for a in catalog.market_apps()
            if (not text or text in (a["name"] or "").casefold() or text in a["package"].casefold())
            and (origin is None or origin in a["origins"])
            and (enabled is None or items.get(a["package"], (None, False))[1] == enabled)]
    counts = {}
    for a in rows:
        if a["category"]:
            counts[a["category"]] = counts.get(a["category"], 0) + 1
    if category is not None:
        rows = [a for a in rows if a["category"] == category]
    rows.sort(key=lambda a: ((a["name"] or a["package"]).casefold(), a["package"]))
    if sort == "bundles":
        rows.sort(key=lambda a: -a["bundle_count"])
    elif sort == "stars":
        rows.sort(key=lambda a: -(a["stars"] or 0))
    elif sort == "updated":
        rows.sort(key=lambda a: a["updated_at"] or "", reverse=True)
    out = []
    for a in rows[(page - 1) * per_page:page * per_page]:
        app_id, on = items.get(a["package"], (None, False))
        out.append({"package": a["package"], "name": a["name"], "category": a["category"], "icon_url": _icon(a["package"]),
                    "bundle_count": a["bundle_count"], "stars": a["stars"], "origins": a["origins"],
                    "updated_at": a["updated_at"], "app_id": app_id, "enabled": on})
    return 200, {"total": len(rows), "page": page, "per_page": per_page,
                 "categories": [{"name": k, "count": n} for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
                 "apps": out, "feed": catalog.feed_status()}


def get_marketplace_app(req):
    pkg = validate_package(req.params["pkg"])
    meta = next((a for a in catalog.market_apps() if a["package"] == pkg), None)
    try:
        bundles = catalog.app_bundles(pkg)
    except KeyError:
        meta = None
    if meta is None:
        raise HttpError(404, f"marketplace: package {pkg} expected in a catalog, found in none")
    app_id, on = _items().get(pkg, (None, False))
    return 200, {"package": pkg, "name": meta["name"], "category": meta["category"], "description": meta["description"],
                 "icon_url": _icon(pkg), "app_id": app_id, "enabled": on,
                 "download": apklocate.download_state(pkg), "bundles": bundles}


def list_apps(req):
    return 200, {"apps": [_with_download(a) for a in apps.list_enabled()]}


def enable_app(req):
    b = req.json
    _keys(b, ENABLE_KEYS, "app enable")
    pkg = validate_package(b.get("package"))
    bundle_id = b.get("bundle_id")
    if not is_int(bundle_id):
        raise HttpError(400, f"app enable {pkg}: bundle_id expected an integer, got {bundle_id!r}")
    patches = b.get("patches")
    if patches is not None and not (isinstance(patches, dict) and all(
            isinstance(k, str) and isinstance(v, bool) for k, v in patches.items())):
        raise HttpError(400, f"app enable {pkg}: patches expected an object of name: true|false, got {patches!r}")
    if b.get("download") is not None:
        apklocate.put_download(pkg, b["download"])
    return 201, _with_download(apps.enable(pkg, bundle_id, patches))


def get_app(req):
    return 200, _with_download(apps.get(_id(req)))


def put_app(req):
    app_id, b = _id(req), req.json
    pkg = apps.get(app_id)["package"]
    before = apklocate.download_state(pkg)
    had_url = bool(before and (before["apkmirror_list_url"] or before["apkpure_url"]))
    if "download" in b:
        apklocate.validate_download(pkg, b["download"])  # both parts are validated before either writes
    settings = {k: v for k, v in b.items() if k != "download"}
    if settings:
        apps.update_settings(app_id, settings)
    if "download" in b:
        apklocate.put_download(pkg, b["download"])
        after = apklocate.download_state(pkg)
        if not had_url and (after["apkmirror_list_url"] or after["apkpure_url"]):
            apps.check_update(app_id)
    return 200, _with_download(apps.get(app_id))


def delete_app(req):
    apps.disable(_id(req))
    return 204, None


def get_patches(req):
    return 200, apps.get_patches(_id(req))


def put_patches(req):
    return 200, apps.put_patches(_id(req), req.json)


def check_app(req):
    return 200, {"job_id": apps.check_update(_id(req))}


def build_app(req):
    return 200, {"job_id": apps.rebuild(_id(req))}


def locate_app(req):
    app_id = _id(req)
    apklocate.reset_lookup(apps.get(app_id)["package"])
    return 200, {"job_id": apps.check_update(app_id)}


def list_artifacts(req):
    return 200, {"artifacts": build.list_artifacts(_id(req))}


def get_schedule(req):
    return 200, apps.get_schedule()


def put_schedule(req):
    return 200, apps.put_schedule(req.json)


def run_schedule(req):
    return 202, {"job_id": apps.run_schedule()}


def catalog_status(req):
    return 200, catalog.feed_status()


class _ServerLog:
    """catalog.refresh log without a job: lines go to the server log."""

    def line(self, text):
        print(f"catalog refresh: {text}", flush=True)


def catalog_refresh(req):
    try:
        catalog.refresh(_ServerLog())
    except catalog.CatalogRefreshBusy as e:
        raise HttpError(409, f"catalog refresh: expected idle, got busy ({e})", "catalog_refresh_busy")
    except catalog.CatalogRefreshFailed as e:
        raise HttpError(502, f"catalog refresh failed: {e}")
    return 200, catalog.feed_status()


def list_sources(req):
    return 200, {"sources": catalog.list_custom()}


def add_source(req):
    b = req.json
    _keys(b, SOURCE_KEYS, "custom source")
    packages = b.get("packages") or []
    if not isinstance(packages, list):
        raise HttpError(400, f"custom source: packages expected a list, got {packages!r}")
    return 201, {"bundle_id": catalog.add_custom(b.get("url"), b.get("engine"), b.get("asset_regex"), tuple(packages))}


def delete_source(req):
    catalog.delete_custom(_id(req, "custom source"))
    return 204, None


ROUTES = [
    ("GET", "/api/marketplace", list_marketplace),
    ("GET", "/api/marketplace/{pkg}", get_marketplace_app),
    ("GET", "/api/apps", list_apps),
    ("POST", "/api/apps", enable_app),
    ("GET", "/api/apps/{id}", get_app),
    ("PUT", "/api/apps/{id}", put_app),
    ("DELETE", "/api/apps/{id}", delete_app),
    ("GET", "/api/apps/{id}/patches", get_patches),
    ("PUT", "/api/apps/{id}/patches", put_patches),
    ("POST", "/api/apps/{id}/check", check_app),
    ("POST", "/api/apps/{id}/build", build_app),
    ("POST", "/api/apps/{id}/locate", locate_app),
    ("GET", "/api/apps/{id}/artifacts", list_artifacts),
    ("GET", "/api/schedule", get_schedule),
    ("PUT", "/api/schedule", put_schedule),
    ("POST", "/api/schedule/run", run_schedule),
    ("GET", "/api/catalog/status", catalog_status),
    ("POST", "/api/catalog/refresh", catalog_refresh),
    ("GET", "/api/sources", list_sources),
    ("POST", "/api/sources", add_source),
    ("DELETE", "/api/sources/{id}", delete_source),
]
