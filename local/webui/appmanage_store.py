"""App Manage store (plan 2026-11-20 §1.2, §1.6, §1.10): per-device app inventory, icons, device action queue and the
primary device setting. Every function writes in one db.tx() and publishes nothing: callers call events.changed."""
import hashlib
import os
import pathlib
from typing import NamedTuple, NoReturn

from . import build, catalog, db, devices, jobs, ota, projects, server, settings
from .server import HttpError, is_int

OPEN = ('queued', 'delivered', 'running')
ERROR_CODES = frozenset(devices.APP_ERROR_CODES) | {
    'PLAY_LOOKUP_FAILED', 'PLAY_DOWNLOAD_FAILED', 'NO_PLAY_PROFILE', 'DEVICE_OFFLINE', 'ADB_ERROR', 'TIMEOUT',
    'UNINSTALL_FAILED', 'ACTIVITY_START_FAILED', 'NOT_INSTALLED', 'REPLACED', 'CANCELLED_BY_USER', 'CANCELLED',
    'DEVICE_ADOPTED'}
# ponytail: fixed at import like the env default db.init() uses in production; tests patch it to their data dir
ICON_DIR = db.DATA / 'icons' / 'appmanage'          # <sha256>.png, content-addressed
ICON_MAX_BYTES = 256 * 1024
PRIMARY_KEY = 'appmanage_primary_device_id'         # settings key, JSON int or null

ACTIONS = ('install', 'upgrade', 'remove')
SOURCES = ('build', 'play_direct', 'play_page')
RESULTS = ('done', 'failed', 'cancelled')
MAX_APPS = 5000
TEXT_MAX = 200                                      # label and size_reason length cap (§1.4)
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
SIZES = ('app_bytes', 'data_bytes', 'cache_bytes')
ENTRY_KEYS = ('package', 'label', 'version_code', 'version_name', *SIZES, 'size_reason', 'icon_sha256')
REF_KIND = {'item': 'items', 'project': 'projects'}   # device_actions.ref_kind -> devices.KINDS key


class Inventory(NamedTuple):
    base_rev: int | None
    full: bool
    apps: dict          # {package: entry without 'package'}
    removed: list


def _bad(field, expected, got) -> NoReturn:
    raise HttpError(400, f'{field} must be {expected}, got {got!r}')


def _text(v, field, nullable):
    if v is None and nullable:
        return
    if not (isinstance(v, str) and 1 <= len(v) <= TEXT_MAX):
        _bad(field, f'a string of 1-{TEXT_MAX} characters' + (' or null' if nullable else ''), v)


def _package(v, field):
    if not (isinstance(v, str) and server.PACKAGE.match(v)):
        _bad(field, 'an Android package name', v)
    return v


def parse_inventory(obj):
    """Boundary parse of the sync "inventory" object (§1.4) -> Inventory."""
    if not isinstance(obj, dict):
        _bad('inventory', 'an object', obj)
    full, base, apps, removed = obj.get('full'), obj.get('base_rev'), obj.get('apps'), obj.get('removed')
    if not isinstance(full, bool):
        _bad('inventory.full', 'true or false', full)
    if not (base is None and full) and not (is_int(base) and base >= 1):
        _bad('inventory.base_rev', 'a positive integer (null only with full=true)', base)
    if not isinstance(apps, list) or len(apps) > MAX_APPS:
        _bad('inventory.apps', f'a list of at most {MAX_APPS} apps', f'{len(apps)} apps' if isinstance(apps, list) else apps)
    if not isinstance(removed, list) or (full and removed):
        _bad('inventory.removed', '[] with full=true' if full else 'a list', removed)
    seen, out = set(), {}
    for i, e in enumerate(apps):
        f = f'inventory.apps[{i}]'
        if not isinstance(e, dict):
            _bad(f, 'an object', e)
        pkg = _package(e.get('package'), f + '.package')
        if pkg in seen:
            _bad(f + '.package', 'unique across apps and removed', pkg)
        seen.add(pkg)
        _text(e.get('label'), f + '.label', nullable=False)
        if not (is_int(e.get('version_code')) and e['version_code'] >= 0):
            _bad(f + '.version_code', 'a non-negative integer', e.get('version_code'))
        if e.get('version_name') is not None and not isinstance(e['version_name'], str):
            _bad(f + '.version_name', 'a string or null', e['version_name'])
        sizes = [e.get(k) for k in SIZES]
        if all(is_int(s) and s >= 0 for s in sizes):
            if e.get('size_reason') is not None:
                _bad(f + '.size_reason', 'null when the sizes are known', e['size_reason'])
        elif all(s is None for s in sizes):
            _text(e.get('size_reason'), f + '.size_reason', nullable=False)
        else:
            _bad(f + '.app_bytes/data_bytes/cache_bytes', 'all non-negative integers or all null', sizes)
        icon = e.get('icon_sha256')
        if icon is not None and not (isinstance(icon, str) and ota.SIGNER.match(icon)):
            _bad(f + '.icon_sha256', '64 lowercase hex characters or null', icon)
        out[pkg] = {'label': e['label'], 'version_code': e['version_code'], 'version_name': e.get('version_name'),
                    **{k: e.get(k) for k in SIZES}, 'size_reason': e.get('size_reason'), 'icon_sha256': icon}
    for i, pkg in enumerate(removed):
        _package(pkg, f'inventory.removed[{i}]')
        if pkg in seen:
            _bad(f'inventory.removed[{i}]', 'unique across apps and removed', pkg)
        seen.add(pkg)
    return Inventory(base, full, out, list(removed))


def _put_apps(c, device_id, apps, now):
    c.executemany('INSERT OR REPLACE INTO device_apps(device_id,package,label,version_code,version_name,app_bytes,'
                  'data_bytes,cache_bytes,size_reason,icon_sha256,reported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  [(device_id, pkg, e['label'], e['version_code'], e['version_name'], *(e[k] for k in SIZES),
                    e['size_reason'], e['icon_sha256'], now) for pkg, e in apps.items()])


def _bump(c, device_id, now):
    r = c.execute('SELECT rev FROM device_app_reports WHERE device_id=?', (device_id,)).fetchone()
    rev = (r['rev'] if r else 0) + 1
    c.execute('INSERT OR REPLACE INTO device_app_reports(device_id,rev,reported_at) VALUES (?,?,?)',
              (device_id, rev, now))
    return rev


def ingest(device_id, inv):
    """-> {'rev', 'accepted', 'need_icons'}. A diff is accepted only on top of the stored rev; not accepted = nothing
    stored (the phone resends full). need_icons = icon hashes of the device's rows that have no file yet."""
    now = db.utcnow()
    with db.tx() as c:
        r = c.execute('SELECT rev FROM device_app_reports WHERE device_id=?', (device_id,)).fetchone()
        if not inv.full and (r is None or r['rev'] != inv.base_rev):
            return {'rev': r['rev'] if r else 0, 'accepted': False, 'need_icons': []}
        if inv.full:
            c.execute('DELETE FROM device_apps WHERE device_id=?', (device_id,))
        c.executemany('DELETE FROM device_apps WHERE device_id=? AND package=?', [(device_id, p) for p in inv.removed])
        _put_apps(c, device_id, inv.apps, now)
        rev = _bump(c, device_id, now)
        hashes = [x[0] for x in c.execute('SELECT DISTINCT icon_sha256 FROM device_apps WHERE device_id=? AND '
                                          'icon_sha256 IS NOT NULL ORDER BY 1', (device_id,))]
    return {'rev': rev, 'accepted': True, 'need_icons': [h for h in hashes if icon_path(h) is None]}


def ingest_full(device_id, apps):
    """ADB: replace all rows of the device with apps {package: entry (§1.4 keys)}; -> new rev."""
    now = db.utcnow()
    with db.tx() as c:
        c.execute('DELETE FROM device_apps WHERE device_id=?', (device_id,))
        _put_apps(c, device_id, apps, now)
        return _bump(c, device_id, now)


def inventory(device_id=None):
    """{device_id: {package: entry}}; a given device_id is always a key (empty when it reported nothing)."""
    out = {} if device_id is None else {device_id: {}}
    where, args = ('WHERE device_id=?', (device_id,)) if device_id is not None else ('', ())
    for r in db.conn().execute(f'SELECT * FROM device_apps {where} ORDER BY device_id, package', args):
        out.setdefault(r['device_id'], {})[r['package']] = {k: r[k] for k in ENTRY_KEYS}
    return out


def reports():
    return {r['device_id']: r['reported_at'] for r in db.conn().execute('SELECT * FROM device_app_reports')}


def store_icon(sha256, png_bytes):
    if not (isinstance(sha256, str) and ota.SIGNER.match(sha256)):
        _bad('sha256', '64 lowercase hex characters', sha256)
    if len(png_bytes) > ICON_MAX_BYTES:
        _bad(f'icon {sha256}', f'at most {ICON_MAX_BYTES} bytes', len(png_bytes))
    if not png_bytes.startswith(PNG_MAGIC):
        _bad(f'icon {sha256}', 'a PNG file', png_bytes[:8])
    got = hashlib.sha256(png_bytes).hexdigest()
    if got != sha256:
        _bad(f'icon {sha256}', f'bytes with sha256 {sha256}', got)
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ICON_DIR / f'.{sha256}.{os.getpid()}.tmp'
    tmp.write_bytes(png_bytes)
    os.replace(tmp, ICON_DIR / f'{sha256}.png')


def icon_path(sha256) -> pathlib.Path | None:
    if not (isinstance(sha256, str) and ota.SIGNER.match(sha256)):
        return None
    p = ICON_DIR / f'{sha256}.png'
    return p if p.is_file() else None


def tracked_refs():
    """{package: {'kind','id','version_code','version_name'}} of the latest Relume build; the item wins over a project."""
    c = db.conn()
    pairs = [(r[0], r[1]) for r in c.execute('SELECT package, id FROM item WHERE package IS NOT NULL ORDER BY id')]
    pairs += [(r[0], r[1]) for r in c.execute(   # package-rename patches: the installed package is the artifact's
        "SELECT DISTINCT package, item_id FROM artifacts WHERE package IS NOT NULL AND package != '' ORDER BY item_id")]
    out = {}
    for pkg, item_id in pairs:
        if pkg in out:
            continue
        arts = [a for a in (build.latest_artifact(item_id, abi) for abi in catalog.ABIS)
                if a and a['version_code'] is not None]
        best = max(arts, key=lambda a: a['version_code'], default=None)
        out[pkg] = {'kind': 'item', 'id': item_id, 'version_code': best and best['version_code'],
                    'version_name': best and best['version_name']}
    for pkg, pid in c.execute('SELECT package, id FROM project WHERE package IS NOT NULL ORDER BY id').fetchall():
        if pkg not in out:
            info = projects.project_info(pid, [])
            out[pkg] = {'kind': 'project', 'id': pid, 'version_code': info['version_code'],
                        'version_name': info['version_name']}
    return out


def _device(c, device_id):
    r = c.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
    if r is None:
        raise HttpError(404, f'device {device_id} not found')
    if devices.is_legacy(r):
        raise HttpError(409, f"device {r['name']} ({device_id}) is a legacy companion row: expected a current device, "
                             'got legacy (update the Relume app on it)')
    return r


def create_actions(package, action, device_ids, source=None):
    """§1.6 create, all-or-nothing in one transaction -> [{'id','device_id','state'}] in device_ids order."""
    _package(package, 'package')
    if action not in ACTIONS:
        _bad('action', ' or '.join(ACTIONS), action)
    server.int_list(device_ids, 'device_ids')
    if not device_ids:
        _bad('device_ids', 'a non-empty list', device_ids)
    if action == 'remove' and source is not None:
        _bad('source', 'absent for remove', source)
    if source is not None and source not in SOURCES:
        _bad('source', ' or '.join(SOURCES), source)
    now, out, adb_ids = db.utcnow(), [], []
    with db.tx() as c:
        ref = tracked_refs().get(package)
        if action != 'remove':
            source = source or ('build' if ref else 'play_direct')
            if source == 'build' and ref is None:
                raise HttpError(400, f'source build needs a Relume-tracked package, {package} is not')
        rk, rid = (ref['kind'], ref['id']) if source == 'build' else (None, None)
        devs = [_device(c, d) for d in device_ids]
        for dev in devs:
            run = c.execute("SELECT action FROM device_actions WHERE device_id=? AND package=? AND state='running'",
                            (dev['id'], package)).fetchone()
            if run:
                raise HttpError(409, f"device {dev['name']} is running {run['action']} of {package}: wait or cancel later")
        for dev in devs:
            old = c.execute("SELECT id FROM device_actions WHERE device_id=? AND package=? AND state IN "
                            "('queued','delivered')", (dev['id'], package)).fetchone()
            if old:
                c.execute("UPDATE device_actions SET state='cancelled', error_code='REPLACED', updated_at=? WHERE id=?",
                          (now, old['id']))
            aid = c.execute('INSERT INTO device_actions(device_id,package,action,source,ref_kind,ref_id,state,created_at,'
                            "updated_at) VALUES (?,?,?,?,?,?,'queued',?,?)",
                            (dev['id'], package, action, source, rk, rid, now, now)).lastrowid
            if old:
                c.execute('UPDATE device_actions SET error_text=? WHERE id=?', (f'replaced by action {aid}', old['id']))
            if rk:
                devices.assign_in(c, dev['id'], REF_KIND[rk], rid)
            elif action == 'remove' and ref:
                devices.unassign_in(c, dev['id'], REF_KIND[ref['kind']], ref['id'])
            if dev['kind'] == 'adb':
                adb_ids.append(aid)
            out.append({'id': aid, 'device_id': dev['id'], 'state': 'queued'})
    for aid in adb_ids:   # after commit: enqueue opens its own transaction; a lost enqueue re-runs at the next refresh
        jobs.enqueue('appmanage', {'action_id': aid}, 'manual')
    return out


def _row(c, action_id):
    r = c.execute('SELECT * FROM device_actions WHERE id=?', (action_id,)).fetchone()
    return dict(r) if r else None


def cancel(action_id):
    """Web DELETE: queued/delivered -> cancelled; failed -> row deleted (dismiss)."""
    with db.tx() as c:
        r = _row(c, action_id)
        if r is None:
            raise HttpError(404, f'action {action_id} not found')
        if r['state'] == 'failed':
            c.execute('DELETE FROM device_actions WHERE id=?', (action_id,))
            return {'id': action_id, 'deleted': True}
        if r['state'] not in ('queued', 'delivered'):
            raise HttpError(409, f"action {action_id} ({r['action']} of {r['package']} on device {r['device_id']}): "
                                 f"expected queued, delivered or failed, got {r['state']}")
        c.execute("UPDATE device_actions SET state='cancelled', error_code='CANCELLED', error_text='cancelled on the web', "
                  'updated_at=? WHERE id=?', (db.utcnow(), action_id))
    return {'id': action_id, 'state': 'cancelled'}


def _wire(r):
    """§1.4 action object."""
    return {'id': r['id'], 'package': r['package'], 'action': r['action'], 'source': r['source'],
            'ref': {'kind': r['ref_kind'], 'id': r['ref_id']} if r['ref_kind'] else None}


def pickup(device_id):
    """queued -> delivered, oldest first; a delivered action is never handed out again."""
    with db.tx() as c:
        rows = c.execute("SELECT * FROM device_actions WHERE device_id=? AND state='queued' ORDER BY id",
                         (device_id,)).fetchall()
        c.executemany("UPDATE device_actions SET state='delivered', updated_at=? WHERE id=?",
                      [(db.utcnow(), r['id']) for r in rows])
    return [_wire(r) for r in rows]


def _mine(c, device_id, action_id, states, want):
    r = _row(c, action_id)
    if r is None or r['device_id'] != device_id:   # same answer for another device's id: no existence leak
        raise HttpError(404, f'action {action_id} not found for this device')
    if r['state'] not in states:
        raise HttpError(409, f"action {action_id} ({r['action']} of {r['package']}): expected {want}, got {r['state']}")
    return r


def start(device_id, action_id):
    with db.tx() as c:
        _mine(c, device_id, action_id, ('delivered',), 'delivered')
        c.execute("UPDATE device_actions SET state='running', updated_at=? WHERE id=?", (db.utcnow(), action_id))
        return _row(c, action_id)


def _outcome(result, error_code, error_text):
    """-> (state, error_code, error_text) of a phone or job result."""
    if result not in RESULTS:
        _bad('result', ' or '.join(RESULTS), result)
    if error_text is not None and not isinstance(error_text, str):
        _bad('error_text', 'a string or null', error_text)
    if result == 'done':
        if error_code is not None:
            _bad('error_code', 'null with result done', error_code)
    elif result == 'cancelled' and error_code is None:
        error_code = 'CANCELLED_BY_USER'
    elif error_code not in ERROR_CODES:
        _bad('error_code', 'one of ' + ', '.join(sorted(ERROR_CODES)), error_code)
    return result, error_code, error_text[-ota.ERROR_TEXT_MAX:] if error_text else error_text


def finish(device_id, action_id, result, error_code=None, error_text=None):
    state, code, text = _outcome(result, error_code, error_text)
    with db.tx() as c:
        _mine(c, device_id, action_id, ('delivered', 'running'), 'delivered or running')
        c.execute('UPDATE device_actions SET state=?, error_code=?, error_text=?, updated_at=? WHERE id=?',
                  (state, code, text, db.utcnow(), action_id))
        return _row(c, action_id)


def finish_by_receipt(device_id, package, result, error_code, error_text):
    """Install receipt (ota.post_install, result installed|failed) -> the device's open build action for package:
    installed -> done, USER_REJECTED -> cancelled/CANCELLED_BY_USER, other failures -> failed. No open action: no-op.
    A failed receipt may carry no code (post_install allows it): the action records INSTALLER_ERROR and says so."""
    if result == 'installed':
        out = _outcome('done', None, None)
    elif error_code == 'USER_REJECTED':
        out = _outcome('cancelled', 'CANCELLED_BY_USER', error_text)
    elif error_code is None:
        out = _outcome('failed', 'INSTALLER_ERROR', f'install receipt failed without an error code: {error_text}')
    else:
        out = _outcome('failed', error_code, error_text)
    with db.tx() as c:
        c.execute("UPDATE device_actions SET state=?, error_code=?, error_text=?, updated_at=? WHERE device_id=? AND "
                  "package=? AND source='build' AND state IN ('queued','delivered','running')",
                  (*out, db.utcnow(), device_id, package))


def claim_for_job(action_id, job_id):
    """ADB: queued -> running with job_id; None when the action is gone or no longer queued."""
    with db.tx() as c:
        n = c.execute("UPDATE device_actions SET state='running', job_id=?, updated_at=? WHERE id=? AND state='queued'",
                      (job_id, db.utcnow(), action_id)).rowcount
        return _row(c, action_id) if n else None


def actions(device_id=None):
    where, args = ('WHERE device_id=?', (device_id,)) if device_id is not None else ('', ())
    return [dict(r) for r in db.conn().execute(f'SELECT * FROM device_actions {where} ORDER BY id DESC', args)]


def get_primary():
    pid = settings.get(PRIMARY_KEY)
    if pid is None or db.conn().execute('SELECT 1 FROM devices WHERE id=?', (pid,)).fetchone() is None:
        return None
    return pid


def set_primary(device_id):
    if device_id is not None and not is_int(device_id):
        _bad('device_id', 'an integer or null', device_id)
    with db.tx() as c:
        if device_id is not None:
            _device(c, device_id)
        settings.put(PRIMARY_KEY, device_id, c)
    return device_id


def move_device(c, from_id, to_id):
    """Adopt (§1.10), inside the caller's tx: the phone row from_id hands its inventory, actions and primary role to
    the ADB row to_id, whose own inventory goes and whose open actions are cancelled."""
    now = db.utcnow()
    for t in ('device_apps', 'device_app_reports'):
        c.execute(f'DELETE FROM {t} WHERE device_id=?', (to_id,))
        c.execute(f'UPDATE {t} SET device_id=? WHERE device_id=?', (to_id, from_id))
    c.execute("UPDATE device_actions SET state='cancelled', error_code='DEVICE_ADOPTED', "
              "error_text=?, updated_at=? WHERE device_id=? AND state IN ('queued','delivered','running')",
              (f'device {to_id} was adopted by the companion app of device {from_id}', now, to_id))
    c.execute('UPDATE device_actions SET device_id=? WHERE device_id=?', (to_id, from_id))
    if settings.get(PRIMARY_KEY) == from_id:
        settings.put(PRIMARY_KEY, to_id, c)
