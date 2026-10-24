"""Catalog: sources, apps, items, groups, patches, job input, seed (spec Part 2)."""
import html
import json
import re
import threading
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import build, db, jobs, projects, scheduler, server, upstream

ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64", "universal")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

ITEM_SQL = """SELECT i.*, s.name AS source_name, s.engine, a.display_name AS app_name, a.icon_file,
  u.id AS u_id, u.repo AS u_repo, u.channel AS u_channel, u.downloaded_tag AS u_downloaded_tag,
  u.latest_tag AS u_latest_tag, u.checked_at AS u_checked_at, u.check_error AS u_check_error
  FROM item i JOIN source s ON s.id = i.source_id JOIN app a ON a.package = i.package
  JOIN upstream u ON u.id = s.upstream_id LEFT JOIN grp g ON g.id = i.group_id"""
ORDER_SQL = " ORDER BY g.position IS NULL, g.position, i.position, i.id"


def version_key(v):
    """Sort key for version strings and tags ('20.12.46', '1.48.0-dev.3', 'v2.0'): one leading v/V is dropped; a part
    of digits is its int, any other part is -1; a trailing 0 makes a release outrank its prerelease ('1.48.0' >
    '1.48.0-dev.3', 'v1.0' > 'v1.0-rc1')."""
    v = v[1:] if v[:1] in ("v", "V") else v
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.\-+_]", v)) + (0,)


def _j(text):
    return None if text is None else json.loads(text)


# ---- target resolution (core §4) ----

def resolve_items(target):
    rows = db.conn().execute(
        "SELECT i.id, i.group_id, i.enabled FROM item i LEFT JOIN grp g ON g.id = i.group_id" + ORDER_SQL).fetchall()
    if target.get("all") is True:
        def keep(r):
            return True
    elif isinstance(target.get("group_ids"), list):
        gids = set(target["group_ids"])

        def keep(r):
            return (r["group_id"] or 0) in gids
    elif isinstance(target.get("item_ids"), list):
        iids = set(target["item_ids"])

        def keep(r):
            return r["id"] in iids
    else:
        return []
    return [r["id"] for r in rows if r["enabled"] and keep(r)]


def resolve_upstreams(target):
    c = db.conn()
    if target.get("all") is True:
        return [r[0] for r in c.execute("SELECT id FROM upstream ORDER BY id")]
    ids = target.get("source_ids")
    if not isinstance(ids, list):
        return []
    out = set()
    for sid in ids:
        r = c.execute("SELECT upstream_id, cli_upstream_id FROM source WHERE id = ?", (sid,)).fetchone()
        if r:
            out.update((r[0], r[1]))
    return sorted(out)


# ---- item object (catalog §6) ----

def _item_obj(r, st):
    variants = json.loads(r["variants"])
    return {
        "id": r["id"],
        "label": r["display_name"] or f'{r["app_name"] or r["package"]} · {r["source_name"]}',
        "display_name": r["display_name"], "source_id": r["source_id"], "source_name": r["source_name"],
        "engine": r["engine"], "package": r["package"], "key": r["key"], "enabled": bool(r["enabled"]),
        "auto_update": bool(r["auto_update"]), "group_id": r["group_id"], "position": r["position"],
        "icon_url": f'/api/apps/{r["package"]}/icon' if r["icon_file"] else None,
        "abis": [v["abi"] for v in variants],
        "upstream": {"id": r["u_id"], "repo": r["u_repo"], "channel": r["u_channel"],
                     "downloaded_tag": r["u_downloaded_tag"], "latest_tag": r["u_latest_tag"],
                     "checked_at": r["u_checked_at"], "check_error": r["u_check_error"]},
        "last_build": st.get("last_build"), "last_success": st.get("last_success"), "active_job": st.get("active_job"),
    }


def item_objects(where_sql="", args=()):
    sql = ITEM_SQL + (" WHERE " + where_sql if where_sql else "") + ORDER_SQL
    rows = db.conn().execute(sql, args).fetchall()
    status = build.item_status([r["id"] for r in rows]) if rows else {}
    return [_item_obj(r, status.get(r["id"]) or {}) for r in rows]


def _groups():
    return [dict(r) for r in db.conn().execute("SELECT id, name, color, position FROM grp ORDER BY position, id")]


def _group(gid):
    r = db.conn().execute("SELECT id, name, color, position FROM grp WHERE id = ?", (gid,)).fetchone()
    if r is None:
        raise server.HttpError(404, "group not found")
    return dict(r)


def catalog_view():
    groups = [dict(g, items=[]) for g in _groups()]
    by_id = {g["id"]: g for g in groups}
    ungrouped = []
    for it in item_objects():
        (by_id[it["group_id"]]["items"] if it["group_id"] in by_id else ungrouped).append(it)
    return {"groups": groups, "ungrouped": ungrouped}


# ---- validation helpers ----

def _name(v, field="name"):
    if not isinstance(v, str) or not v.strip() or len(v) > 80:
        raise server.HttpError(400, f"invalid {field}")
    return v.strip()


def _check_color(v):
    if v is not None and not (isinstance(v, str) and COLOR_RE.match(v)):
        raise server.HttpError(400, "invalid color")
    return v


# ---- group routes (catalog §3.1) ----

def get_catalog(req):
    return 200, catalog_view()


def list_groups(req):
    return 200, {"groups": _groups()}


def _next_position(c, table_where, args=()):
    return c.execute(f"SELECT IFNULL(MAX(position), -1) + 1 FROM {table_where}", args).fetchone()[0]


def post_group(req):
    b = req.json or {}
    name, color = _name(b.get("name")), _check_color(b.get("color"))
    with db.tx() as c:
        pos = _next_position(c, "grp")
        gid = c.execute("INSERT INTO grp(name, color, position) VALUES (?, ?, ?)", (name, color, pos)).lastrowid
    return 201, {"id": gid}


def put_group(req):
    gid = req.params["id"]
    _group(gid)
    b = req.json or {}
    with db.tx() as c:
        if "name" in b:
            c.execute("UPDATE grp SET name = ? WHERE id = ?", (_name(b["name"]), gid))
        if "color" in b:
            c.execute("UPDATE grp SET color = ? WHERE id = ?", (_check_color(b["color"]), gid))
    return 200, _group(gid)


def delete_group(req):
    gid = req.params["id"]
    _group(gid)
    with db.tx() as c:
        start = _next_position(c, "item WHERE group_id IS NULL")
        moved = [r[0] for r in c.execute("SELECT id FROM item WHERE group_id = ? ORDER BY position, id", (gid,))]
        for n, iid in enumerate(moved):
            c.execute("UPDATE item SET group_id = NULL, position = ? WHERE id = ?", (start + n, iid))
        c.execute("DELETE FROM grp WHERE id = ?", (gid,))
        for n, (g,) in enumerate(c.execute("SELECT id FROM grp ORDER BY position, id").fetchall()):
            c.execute("UPDATE grp SET position = ? WHERE id = ?", (n, g))
    return 200, {"ok": True}


def put_groups_order(req):
    ids = server.int_list((req.json or {}).get("ids"), "ids")
    with db.tx() as c:
        if set(ids) != {r[0] for r in c.execute("SELECT id FROM grp")}:
            raise server.HttpError(400, "ids must list every group once")
        for n, g in enumerate(ids):
            c.execute("UPDATE grp SET position = ? WHERE id = ?", (n, g))
    return 200, {"ok": True}


def put_group_items_order(req):
    gid = req.params["id"]
    if gid != 0:
        _group(gid)
    ids = server.int_list((req.json or {}).get("item_ids"), "item_ids")
    target = None if gid == 0 else gid
    with db.tx() as c:
        if not set(ids) <= {r[0] for r in c.execute("SELECT id FROM item")}:
            raise server.HttpError(400, "unknown item id")
        rest = [r[0] for r in c.execute("SELECT id FROM item WHERE group_id IS ? ORDER BY position, id", (target,))
                if r[0] not in ids]
        for n, iid in enumerate(ids + rest):
            c.execute("UPDATE item SET group_id = ?, position = ? WHERE id = ?", (target, n, iid))
    return 200, {"ok": True}



KEYSTORE_DIR = db.REPO_ROOT / "src" / "keystore"
URL_RE = re.compile(r"^https://[^\s\"'<>]+$")
ARCH_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9 ._+-]*)?$")   # no leading . or - (grep option); seed has 'arm64-v8a + armeabi-v7a' (icysymmetra.sh)
MINVER_RE = re.compile(r"^[A-Za-z0-9 .+-]*$")
DOWNLOAD_DEFAULT = {"kind": "apkmirror", "type": "apk", "arch": None, "dpi": None, "minver": None, "url": None}
APP_URL_FIELDS = ("apkmirror_list_url", "apkmirror_example_url", "apkmirror_example_url2", "apkpure_url")


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
        if (not isinstance(d, dict) or d.get("kind") not in ("apkmirror", "apkpure", "url")
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


# --- Xposed module items (2026-10-19-webui-design.md) ---
ENGINES = {'morphe': {'kind': 'patches'},
           'lspatch': {'kind': 'module', 'max_sigbypass': 3},
           'npatch': {'kind': 'module', 'max_sigbypass': 4}}


def is_module(engine):
    return ENGINES.get(engine, {}).get('kind') == 'module'


def _engine_of_source(source_id):
    r = db.conn().execute('SELECT engine FROM source WHERE id=?', (source_id,)).fetchone()
    return r['engine'] if r else None


def _engine_of_item(item_id):
    r = db.conn().execute('SELECT s.engine FROM item i JOIN source s ON s.id = i.source_id WHERE i.id=?',
                          (item_id,)).fetchone()
    return r['engine'] if r else None


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


def xposed_item_rules(body, item_id=None):
    """POST /api/items (item_id None) and PUT /api/items/{id}; normalizes body['engine_options'] in place."""
    if not isinstance(body, dict):
        return
    if body.get('source_id') is not None:
        engine = _engine_of_source(body['source_id'])
    else:
        engine = _engine_of_item(item_id) if item_id is not None else None
    if engine is None:
        return  # unknown source or item: the caller's own checks answer 400/404
    if item_id is None and is_module(engine):  # POST defaults for module items
        body.setdefault('engine_options', {'sigbypass': 0, 'injectdex': False})
        if body.get('variants') is None:
            body['variants'] = [dict(v, strip_libs=None) for v in default_variants()]
    if item_id is None or 'engine_options' in body:
        body['engine_options'] = validate_engine_options(engine, body.get('engine_options'))
    if is_module(engine) and 'variants' in body:
        check_xposed_variants(body['variants'])


def xposed_source_rules(body, source_id=None):
    """POST /api/sources (source_id None) and PUT /api/sources/{id}."""
    if not isinstance(body, dict):
        return
    engine = body.get('engine') or (_engine_of_source(source_id) if source_id else 'morphe')
    if engine != 'morphe' and not body.get('asset_regex') and (source_id is None or 'repo' in body):
        raise server.HttpError(400, 'asset_regex required for module sources')


def reject_module_item(item_id):
    if is_module(_engine_of_item(item_id)):
        raise server.HttpError(409, 'not a patches item')


def check_version_mode(item_id, body):
    if isinstance(body, dict) and body.get('version_mode') == 'auto' and is_module(_engine_of_item(item_id)):
        raise server.HttpError(400, 'auto version not available for module items')


def display_name(v):
    return None if v is None else _name(v, "display_name")


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


# ---- sources (catalog §6) ----

def _source(sid):
    r = db.conn().execute("""SELECT s.*, u.repo, u.channel, u.tag, u.asset_regex FROM source s
        JOIN upstream u ON u.id = s.upstream_id WHERE s.id = ?""", (sid,)).fetchone()
    if r is None:
        raise server.HttpError(404, "source not found")
    return dict(r)


def _source_fields(b):
    engine = b.get("engine", "morphe")
    if engine not in ENGINES:
        raise server.HttpError(400, "invalid engine")
    channel = b.get("channel")
    if channel not in ("stable", "prerelease", "tag"):
        raise server.HttpError(400, "invalid channel")
    tag = b.get("tag")
    if channel == "tag":
        tag = server.validate_slug(tag, "tag")
    elif tag is not None:
        raise server.HttpError(400, "tag is only for channel tag")
    rx = b.get("asset_regex")
    if rx is not None:
        try:
            re.compile(rx)
        except (re.error, TypeError):
            raise server.HttpError(400, "invalid asset_regex")
    ks = server.validate_slug(b.get("keystore", "morphe.keystore"), "keystore")
    if not (KEYSTORE_DIR / ks).is_file():
        raise server.HttpError(400, "keystore not found")
    cli = b.get("cli_upstream_id")
    r = db.conn().execute("SELECT kind FROM upstream WHERE id = ?", (cli,)).fetchone() if server.is_int(cli) else None
    if r is None or r["kind"] != "cli":
        raise server.HttpError(400, "invalid cli_upstream_id")
    return {"name": _name(b.get("name")), "engine": engine, "repo": server.validate_repo(b.get("repo"), "repo"),
            "channel": channel, "tag": tag, "asset_regex": rx, "keystore": ks, "cli_upstream_id": cli}


def list_sources(req):
    rows = db.conn().execute("SELECT id FROM source ORDER BY id").fetchall()
    return 200, {"sources": [_source(r[0]) for r in rows]}


def post_source(req):
    xposed_source_rules(req.json)
    b = req.json or {}
    sid = server.validate_slug(b.get("id"), "id")
    f = _source_fields(b)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM source WHERE id = ?", (sid,)).fetchone():
            raise server.HttpError(409, "source exists")
        uid = upstream_row(c, ENGINES[f["engine"]]["kind"], f["repo"], f["channel"], f["tag"], f["asset_regex"])
        c.execute("INSERT INTO source(id, name, upstream_id, cli_upstream_id, engine, keystore) VALUES (?, ?, ?, ?, ?, ?)",
                  (sid, f["name"], uid, f["cli_upstream_id"], f["engine"], f["keystore"]))
    return 201, {"id": sid}


def put_source(req):
    xposed_source_rules(req.json, req.params["id"])
    sid = req.params["id"]
    _source(sid)
    f = _source_fields(req.json or {})
    with db.tx() as c:
        uid = upstream_row(c, ENGINES[f["engine"]]["kind"], f["repo"], f["channel"], f["tag"], f["asset_regex"])
        c.execute("UPDATE source SET name = ?, upstream_id = ?, cli_upstream_id = ?, engine = ?, keystore = ? WHERE id = ?",
                  (f["name"], uid, f["cli_upstream_id"], f["engine"], f["keystore"], sid))
    return 200, _source(sid)


def delete_source(req):
    sid = req.params["id"]
    _source(sid)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM item WHERE source_id = ?", (sid,)).fetchone():
            raise server.HttpError(409, "source has items")
        c.execute("DELETE FROM source WHERE id = ?", (sid,))
    return 200, {"ok": True}


# ---- items (catalog §6) ----

def _group_id(v):
    if v is None or v == 0:
        return None
    if not server.is_int(v):
        raise server.HttpError(400, "invalid group_id")
    _group(v)
    return v


def _default_key(package):
    key = re.sub(r"[^a-z0-9._-]", "-", package.rsplit(".", 1)[-1].lower()).lstrip(".-")
    return key or "app"


def item_detail(iid):
    objs = item_objects("i.id = ?", (iid,))
    if not objs:
        raise server.HttpError(404, "item not found")
    r = db.conn().execute("""SELECT i.version_mode, i.version_pin, i.allow_nearest, i.variants, i.engine_options,
        a.apkmirror_versions, a.apkmirror_checked_at FROM item i JOIN app a ON a.package = i.package WHERE i.id = ?""",
                          (iid,)).fetchone()
    deps = projects.item_projects(iid)
    o = objs[0]
    o.update(version={"mode": r["version_mode"], "pin": r["version_pin"], "allow_nearest": bool(r["allow_nearest"])},
             variants=json.loads(r["variants"]), engine_options=_j(r["engine_options"]), project_ids=deps,
             apkmirror={"versions": _j(r["apkmirror_versions"]), "checked_at": r["apkmirror_checked_at"]})
    return o


def list_items(req):
    return 200, {"items": item_objects()}  # list responses are wrapped


def get_item(req):
    return 200, item_detail(req.params["id"])


def post_item(req):
    xposed_item_rules(req.json)
    b = req.json or {}
    src = _source(server.validate_slug(b.get("source_id"), "source_id")) if isinstance(b.get("source_id"), str) else None
    if src is None:
        raise server.HttpError(400, "invalid source_id")
    pkg = server.validate_package(b.get("package"), "package")
    gid = _group_id(b.get("group_id"))
    variants = validate_variants(b["variants"]) if b.get("variants") is not None else default_variants()
    eo = b.get("engine_options")
    dn = display_name(b.get("display_name"))
    with db.tx() as c:
        if c.execute("SELECT 1 FROM item WHERE source_id = ? AND package = ?", (src["id"], pkg)).fetchone():
            raise server.HttpError(409, "item exists")
        c.execute("INSERT OR IGNORE INTO app(package) VALUES (?)", (pkg,))
        pos = _next_position(c, "item WHERE group_id IS ?", (gid,))
        iid = c.execute("""INSERT INTO item(source_id, package, key, display_name, group_id, position, variants, engine_options,
            enabled, auto_update) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)""", (src["id"], pkg, _default_key(pkg), dn, gid, pos, json.dumps(variants),
                                                 None if eo is None else json.dumps(eo))).lastrowid
        up = c.execute("SELECT kind, downloaded_tag FROM upstream WHERE id = ?", (src["upstream_id"],)).fetchone()
        need_pull = bool(up["kind"] == "patches" and up["downloaded_tag"] and not c.execute(
            "SELECT 1 FROM bundle_info WHERE upstream_id = ? AND tag = ? AND package = ?",
            (src["upstream_id"], up["downloaded_tag"], pkg)).fetchone())
    if need_pull:
        jobs.enqueue("upstream", {"upstream_id": src["upstream_id"]}, "manual", options={"rebuild": False})
    return 201, {"id": iid}


def put_item(req):
    xposed_item_rules(req.json, req.params["id"])
    iid = req.params["id"]
    cur = item_detail(iid)
    b = req.json or {}
    for fixed in ("source_id", "package"):
        if fixed in b and b[fixed] != cur[fixed]:
            raise server.HttpError(400, f"{fixed} cannot change")
    sets = {}
    if "display_name" in b:
        sets["display_name"] = display_name(b["display_name"])
    if "variants" in b:
        sets["variants"] = json.dumps(validate_variants(b["variants"]))
    if "engine_options" in b:
        eo = b["engine_options"]
        sets["engine_options"] = None if eo is None else json.dumps(eo)
    for flag in ("enabled", "auto_update"):
        if flag in b:
            if not isinstance(b[flag], bool):
                raise server.HttpError(400, f"invalid {flag}")
            sets[flag] = int(b[flag])
    deps = None
    if "project_ids" in b:
        deps = server.int_list(b["project_ids"], "project_ids")
        known = {r[0] for r in db.conn().execute("SELECT id FROM project")}
        if not set(deps) <= known:
            raise server.HttpError(400, "unknown project")
    with db.tx() as c:
        if "group_id" in b:
            gid = _group_id(b["group_id"])
            if gid != cur["group_id"]:
                sets["group_id"] = gid
                sets["position"] = _next_position(c, "item WHERE group_id IS ?", (gid,))
        for col, val in sets.items():  # col comes from the fixed names above
            c.execute(f"UPDATE item SET {col} = ? WHERE id = ?", (val, iid))
        if deps is not None:
            c.execute("DELETE FROM item_dependency WHERE item_id = ?", (iid,))
            c.executemany("INSERT INTO item_dependency(item_id, project_id) VALUES (?, ?)", [(iid, d) for d in deps])
    return 200, item_detail(iid)


def delete_item(req):
    iid = req.params["id"]
    item_detail(iid)
    st = build.item_status([iid]).get(iid) or {}
    if st.get("active_job"):
        raise server.HttpError(409, "build queued or running")
    build.forget_item(iid)
    with db.tx() as c:
        c.execute("DELETE FROM item WHERE id = ?", (iid,))
    return 200, {"ok": True}


# ---- apps ----

def _app(pkg):
    r = db.conn().execute("SELECT package, display_name, " + ", ".join(APP_URL_FIELDS) +
                          " FROM app WHERE package = ?", (pkg,)).fetchone()
    if r is None:
        raise server.HttpError(404, "app not found")
    return dict(r)


def put_app(req):
    pkg = server.validate_package(req.params["pkg"], "package")
    _app(pkg)
    b = req.json or {}
    with db.tx() as c:
        if "display_name" in b:
            c.execute("UPDATE app SET display_name = ? WHERE package = ?", (display_name(b["display_name"]), pkg))
        for f in APP_URL_FIELDS:  # f from the fixed tuple
            if f in b:
                c.execute(f"UPDATE app SET {f} = ? WHERE package = ?", (_opt(b[f], URL_RE, f), pkg))
    return 200, _app(pkg)


class UpstreamMissing(Exception):
    """A needed upstream has no downloaded tag (build fails upstream_missing)."""


def _item_row(iid):
    r = db.conn().execute("SELECT i.*, s.upstream_id, s.cli_upstream_id, s.engine FROM item i JOIN source s ON s.id = i.source_id WHERE i.id = ?",
                          (iid,)).fetchone()
    if r is None:
        raise server.HttpError(404, "item not found")
    return r


def _bundle(r):
    """(tag, file names, bundle patches or None) for the item's downloaded patches tag."""
    tag = db.conn().execute("SELECT downloaded_tag FROM upstream WHERE id = ?", (r["upstream_id"],)).fetchone()[0]
    if not tag:
        return None, [], None
    d = upstream.tag_dir(r["upstream_id"], tag)
    files = sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []
    b = db.conn().execute("SELECT patches FROM bundle_info WHERE upstream_id = ? AND tag = ? AND package = ?",
                          (r["upstream_id"], tag, r["package"])).fetchone()
    return tag, files, (json.loads(b[0]) if b else None)


def get_patches(req):
    reject_module_item(req.params["id"])
    r = _item_row(req.params["id"])
    tag, files, bundle = _bundle(r)
    ov = json.loads(r["patch_overrides"])
    if bundle is None:
        return 200, {"tag": tag, "files": files, "patches": [], "stale_overrides": [], "compatible_versions": [], "unavailable": True}
    names = {p["name"] for p in bundle}
    patches = []
    for p in bundle:
        o = ov.get(p["name"], {})
        values = o.get("options", {})
        patches.append({"name": p["name"], "description": p["description"], "default_enabled": p["default_enabled"],
                        "enabled": o.get("enabled", p["default_enabled"]),
                        "options": [dict(x, value=values.get(x["key"], x["default"])) for x in p["options"]],
                        "compatible_versions": p["compatible_versions"]})
    union = sorted({v for p in bundle for v in p["compatible_versions"]}, key=version_key, reverse=True)
    return 200, {"tag": tag, "files": files, "patches": patches, "stale_overrides": sorted(n for n in ov if n not in names),
                 "compatible_versions": union, "unavailable": False}


def _type_ok(t, v):
    if v is None:
        return True
    t = (t or "").split("<")[0].rsplit(".", 1)[-1].lower()  # CLI prints kotlin.String, kotlin.collections.List<…>
    if t.endswith("array") or t.endswith("list") or t.startswith("list"):
        return isinstance(v, list)
    if t in ("boolean", "bool"):
        return isinstance(v, bool)
    if t in ("int", "integer", "long"):
        return server.is_int(v)
    if t in ("float", "double"):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "string":
        return isinstance(v, str)
    return isinstance(v, (str, int, float, bool, list))


def put_patches(req):
    reject_module_item(req.params["id"])
    r = _item_row(req.params["id"])
    _, _, bundle = _bundle(r)
    if bundle is None:
        raise server.HttpError(409, "patch list unavailable")
    ov = (req.json or {}).get("overrides")
    if not isinstance(ov, dict):
        raise server.HttpError(400, "invalid overrides")
    by_name = {p["name"]: p for p in bundle}
    # stale entries (not in this bundle) stay server-side; the body replaces the bundle entries ({} = reset)
    clean = {n: v for n, v in json.loads(r["patch_overrides"]).items() if n not in by_name}
    for name, o in ov.items():
        if name not in by_name:
            raise server.HttpError(400, f"unknown patch: {name}")
        if not isinstance(o, dict) or set(o) - {"enabled", "options"}:
            raise server.HttpError(400, f"invalid override: {name}")
        e = {}
        if "enabled" in o:
            if not isinstance(o["enabled"], bool):
                raise server.HttpError(400, f"invalid enabled: {name}")
            e["enabled"] = o["enabled"]
        if "options" in o:
            if not isinstance(o["options"], dict):
                raise server.HttpError(400, f"invalid options: {name}")
            types = {x["key"]: x["type"] for x in by_name[name]["options"]}
            for k, v in o["options"].items():
                if k not in types:
                    raise server.HttpError(400, f"unknown option: {name}.{k}")
                if not _type_ok(types[k], v):
                    raise server.HttpError(400, f"invalid value: {name}.{k}")
            e["options"] = o["options"]
        clean[name] = e
    with db.tx() as c:
        c.execute("UPDATE item SET patch_overrides = ? WHERE id = ?", (json.dumps(clean, sort_keys=True), r["id"]))
    return get_patches(req)


def put_version(req):
    check_version_mode(req.params["id"], req.json)
    r = _item_row(req.params["id"])
    b = req.json or {}
    mode = b.get("version_mode")
    if mode not in ("auto", "pinned", "latest"):
        raise server.HttpError(400, "invalid version_mode")
    pin = server.validate_slug(b.get("version_pin"), "version_pin") if mode == "pinned" else None
    near = b.get("allow_nearest", False)
    if not isinstance(near, bool):
        raise server.HttpError(400, "invalid allow_nearest")
    with db.tx() as c:
        c.execute("UPDATE item SET version_mode = ?, version_pin = ?, allow_nearest = ? WHERE id = ?", (mode, pin, int(near), r["id"]))
    return 200, item_detail(r["id"])


def stale_overrides(item_id):
    r = _item_row(item_id)
    _, _, bundle = _bundle(r)
    if bundle is None:
        return []
    names = {p["name"] for p in bundle}
    return sorted(n for n in json.loads(r["patch_overrides"]) if n not in names)


def _versions_due(checked_at):
    if checked_at is None:
        return True
    t = db.parse_iso(checked_at)
    return t < datetime.now(timezone.utc) - timedelta(hours=24)


def job_input(item_id):
    r = db.conn().execute("""SELECT i.*, s.engine, s.keystore, s.upstream_id, s.cli_upstream_id,
        a.apkmirror_list_url, a.apkmirror_example_url, a.apkmirror_example_url2, a.apkpure_url, a.apkmirror_checked_at,
        u.downloaded_tag AS up_tag, c.downloaded_tag AS cli_tag
        FROM item i JOIN source s ON s.id = i.source_id JOIN app a ON a.package = i.package
        JOIN upstream u ON u.id = s.upstream_id JOIN upstream c ON c.id = s.cli_upstream_id WHERE i.id = ?""",
                          (item_id,)).fetchone()
    if r is None:
        raise UpstreamMissing(f"item {item_id} not found")
    if not r["up_tag"]:
        raise UpstreamMissing(f"source upstream {r['upstream_id']} not downloaded")
    if not r["cli_tag"]:
        raise UpstreamMissing(f"cli upstream {r['cli_upstream_id']} not downloaded")
    pdir, cdir = upstream.tag_dir(r["upstream_id"], r["up_tag"]), upstream.tag_dir(r["cli_upstream_id"], r["cli_tag"])
    files = sorted(str(p) for p in pdir.iterdir() if p.is_file()) if pdir.is_dir() else []
    jars = sorted(str(p) for p in cdir.glob("*.jar")) if cdir.is_dir() else []
    if not files or not jars:
        raise UpstreamMissing("downloaded files missing on disk")
    options = None
    if r["engine"] == "morphe":
        stale = set(stale_overrides(item_id))
        ov = {k: v for k, v in json.loads(r["patch_overrides"]).items() if k not in stale}
        options = [{"patches": ov} for _ in files]
    return {
        "item_id": r["id"], "source_id": r["source_id"], "package": r["package"], "key": r["key"],
        "engine": r["engine"], "engine_options": _j(r["engine_options"]),
        "upstream_tag": r["up_tag"], "cli_tag": r["cli_tag"], "patches_files": files, "cli_jar": jars[0],
        "options_file_json": options,
        "version": {"mode": r["version_mode"], "value": r["version_pin"], "near": bool(r["allow_nearest"])},
        "apkmirror": {"list_url": r["apkmirror_list_url"], "example_url": r["apkmirror_example_url"],
                      "example_url2": r["apkmirror_example_url2"], "versions_due": _versions_due(r["apkmirror_checked_at"])},
        "apkpure_url": r["apkpure_url"], "keystore": r["keystore"], "recipe": {"variants": json.loads(r["variants"])},
    }


def set_apkmirror_versions(package, versions):
    with db.tx() as c:
        c.execute("UPDATE app SET apkmirror_versions = ?, apkmirror_checked_at = ? WHERE package = ?",
                  (json.dumps(list(versions)), db.utcnow(), package))


PLAY_URL = "https://play.google.com/store/apps/details?id={}&hl=en"
OG_RE = re.compile(r'<meta[^>]+property="og:(title|image)"[^>]+content="([^"]*)"')
ICON_EXT = {"image/png": "png", "image/webp": "webp", "image/jpeg": "jpg"}
_icon_wake = threading.Event()


def fetch_icon(package):
    """Play og:title / og:image (catalog §3.2-3.3). False on any failure (UI shows a letter tile)."""
    now = db.utcnow()
    try:
        st, _, body = upstream.http_get(PLAY_URL.format(package), {"User-Agent": "Mozilla/5.0"}, timeout=20)
        if st != 200:
            raise ValueError(f"play http {st}")
        og = dict(OG_RE.findall(body.decode("utf-8", "replace")))
        title = html.unescape(og.get("title", "")).removesuffix(" - Apps on Google Play").strip()
        img = html.unescape(og.get("image", ""))
        if not img.startswith("https://"):
            raise ValueError("no og:image")
        st, headers, data = upstream.http_get(img, {"User-Agent": "Mozilla/5.0"}, timeout=20)
        ext = ICON_EXT.get(headers.get("content-type", "").split(";")[0].strip())
        if st != 200 or not ext or not data:
            raise ValueError("bad icon response")
        d = db.DATA / "icons"
        d.mkdir(parents=True, exist_ok=True)
        name = f"{package}.{ext}"
        (d / name).write_bytes(data)
    except (OSError, ValueError):
        with db.tx() as c:
            c.execute("UPDATE app SET icon_fetched_at = ? WHERE package = ?", (now, package))
        return False
    with db.tx() as c:
        c.execute("UPDATE app SET icon_file = ?, icon_fetched_at = ? WHERE package = ?", (name, now, package))
        if title:
            c.execute("UPDATE app SET display_name = ? WHERE package = ? AND display_name IS NULL", (title, package))
    return True


def _icon_loop():
    while True:
        try:
            for (pkg,) in db.conn().execute("SELECT package FROM app WHERE icon_fetched_at IS NULL ORDER BY package").fetchall():
                fetch_icon(pkg)
        except Exception:  # keep the fetcher alive; the error goes to the server log
            traceback.print_exc()
        _icon_wake.wait(3600)
        _icon_wake.clear()


def start():
    threading.Thread(target=_icon_loop, name="catalog-icons", daemon=True).start()


def get_icon(req):
    pkg = server.validate_package(req.params["pkg"], "package")
    r = db.conn().execute("SELECT icon_file FROM app WHERE package = ?", (pkg,)).fetchone()
    path = db.DATA / "icons" / r["icon_file"] if r and r["icon_file"] else None
    if path is None or not path.is_file():
        raise server.HttpError(404, "no icon")
    return server.FileResponse(str(path), cache_seconds=86400)


def post_icon_refresh(req):
    pkg = server.validate_package(req.params["pkg"], "package")
    _app(pkg)
    with db.tx() as c:
        c.execute("UPDATE app SET icon_fetched_at = NULL WHERE package = ?", (pkg,))
    _icon_wake.set()
    return 200, {"ok": True}


SEED_PATH = Path(__file__).with_name("catalog-seed.json")
# seed key -> (table, columns, order) ; insert order follows the foreign keys
SEED_TABLES = {
    "upstreams": ("upstream", ("id", "kind", "repo", "channel", "tag", "asset_regex"), "id"),
    "apps": ("app", ("package", "display_name", "apkmirror_list_url", "apkmirror_example_url",
                     "apkmirror_example_url2", "apkpure_url"), "package"),
    "sources": ("source", ("id", "name", "upstream_id", "cli_upstream_id", "engine", "keystore"), "id"),
    "groups": ("grp", ("id", "name", "color", "position"), "id"),
    "items": ("item", ("id", "source_id", "package", "key", "display_name", "group_id", "position", "enabled",
                       "version_mode", "version_pin", "allow_nearest", "variants", "patch_overrides", "engine_options"), "id"),
    "projects": ("project", ("id", "upstream_id", "package", "display_name", "auto_push"), "id"),
}
JSON_COLS = {"variants", "patch_overrides", "engine_options"}
BOOL_COLS = {"enabled", "allow_nearest", "auto_push"}
DAILY_SCHEDULE = ("Daily update", "app", {"all": True}, {"every": "day", "at": "09:00"},
                  {"update": True, "build": True, "force": False, "notify": True})


def _to_db(col, v):
    if col in JSON_COLS:
        return None if v is None else json.dumps(v, sort_keys=True)
    if col in BOOL_COLS:
        return int(bool(v))
    return v


def _from_db(col, v):
    if col in JSON_COLS:
        return None if v is None else json.loads(v)
    if col in BOOL_COLS:
        return bool(v)
    return v


def seed_import(path=None):
    """Import the seed only into empty catalog tables (catalog §8). True when imported."""
    data = json.loads(Path(path or SEED_PATH).read_text())
    with db.tx() as c:
        if c.execute("SELECT EXISTS(SELECT 1 FROM source) OR EXISTS(SELECT 1 FROM item)").fetchone()[0]:
            return False
        for key, (table, cols, _) in SEED_TABLES.items():  # names from the fixed table above
            for row in data.get(key, []):
                c.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          [_to_db(k, row.get(k)) for k in cols])
        c.executemany("INSERT INTO item_dependency(item_id, project_id) VALUES (?, ?)",
                      [tuple(x) for x in data.get("item_dependencies", [])])
    name, kind, target, spec, options = DAILY_SCHEDULE
    if not db.conn().execute("SELECT 1 FROM schedules WHERE kind = ? AND name = ?", (kind, name)).fetchone():
        scheduler.create(name, kind, target, spec, options)  # computes next_run_at
    return True


def export_seed():
    c = db.conn()
    out = {}
    for key, (table, cols, order) in SEED_TABLES.items():
        out[key] = [{k: _from_db(k, r[k]) for k in cols}
                    for r in c.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}")]
    out["item_dependencies"] = [list(r) for r in c.execute(
        "SELECT item_id, project_id FROM item_dependency ORDER BY item_id, project_id")]
    return out


def seed_text():
    """Exact bytes of catalog-seed.json; `python3 -m webui export-seed` prints this (ci §4.4)."""
    return json.dumps(export_seed(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


ROUTES = [
    ("GET", "/api/catalog", get_catalog),
    ("GET", "/api/groups", list_groups),
    ("POST", "/api/groups", post_group),
    ("PUT", "/api/groups/order", put_groups_order),
    ("PUT", "/api/groups/{id}", put_group),
    ("DELETE", "/api/groups/{id}", delete_group),
    ("PUT", "/api/groups/{id}/items/order", put_group_items_order),
    ("GET", "/api/sources", list_sources),
    ("POST", "/api/sources", post_source),
    ("PUT", "/api/sources/{id}", put_source),
    ("DELETE", "/api/sources/{id}", delete_source),
    ("GET", "/api/items", list_items),
    ("POST", "/api/items", post_item),
    ("GET", "/api/items/{id}", get_item),
    ("PUT", "/api/items/{id}", put_item),
    ("DELETE", "/api/items/{id}", delete_item),
    ("PUT", "/api/apps/{pkg}", put_app),
    ("GET", "/api/items/{id}/patches", get_patches),
    ("PUT", "/api/items/{id}/patches", put_patches),
    ("PUT", "/api/items/{id}/version", put_version),
    ("GET", "/api/apps/{pkg}/icon", get_icon),
    ("POST", "/api/apps/{pkg}/icon/refresh", post_icon_refresh),
]
