"""OTA part: planner, read model, OTA job handler, recovery routes, after-build OTA (spec device 3-4,
spec follow-project 5.4)."""
import hashlib
import json
import os
import re
from typing import NamedTuple

from . import build, catalog, db, devices, events, jobs, projects, server
from .server import HttpError, is_int


def abis_of(dev):
    return [a.strip() for a in (dev.get('abi_list') or '').split(',') if a.strip()] or ['universal']


def first_abi(dev):
    return abis_of(dev)[0]


def assigned_items(device_id, item_ids=None):
    """Enabled assigned items in item id order: a removed (disabled) app is never synced."""
    rows = db.conn().execute(
        "SELECT item.id AS item_id, item.package, COALESCE(item.display_name, store_app.name, item.package) AS display_name "
        'FROM device_assignments da JOIN item ON item.id = da.item_id '
        'LEFT JOIN store_app ON store_app.package = item.package WHERE da.device_id = ? AND item.enabled = 1 ORDER BY item.id',
        (device_id,)).fetchall()
    items = [dict(r) for r in rows]
    if item_ids is not None:
        wanted = set(item_ids)
        items = [i for i in items if i['item_id'] in wanted]
    return items


def assigned_item_ids(device_id):
    """Every assigned item id, removed (disabled) apps included: a phone or page may still hold such an id, and
    assigned_items then drops it from the plan."""
    return {r[0] for r in db.conn().execute('SELECT item_id FROM device_assignments WHERE device_id = ?', (device_id,))}


def last_installs(device_id):
    """-> (items, projects): newest installed/up_to_date receipt per item id and per project id (history: decision 15
    makes install_records the deciding record). Item records carry the build's patch version (decision 14)."""
    items, projects = {}, {}
    rows = db.conn().execute(
        "SELECT o.kind, o.ref_id, o.build_job_id, o.upstream_tag, o.version_name, o.version_code, b.patches_version "
        "FROM ota_installs o LEFT JOIN build_jobs b ON b.job_id = o.build_job_id "
        "WHERE o.device_id = ? AND o.result IN ('installed','up_to_date') ORDER BY o.id", (device_id,))
    for r in rows:  # oldest first: newer rows overwrite
        if r['kind'] == 'item':
            items[r['ref_id']] = {'build_job_id': r['build_job_id'], 'version_name': r['version_name'],
                                  'version_code': r['version_code'], 'patch_version': r['patches_version']}
        else:
            projects[r['ref_id']] = {'upstream_tag': r['upstream_tag']}
    return items, projects


def _reported_hash(present, package):
    return (present or {}).get(package, {}).get('apk_sha256')


def install_records(device_id, arts, infos, present):
    """-> (items, projects): the server's install record per item/project id on this phone (decisions 14, 15).
    A reported apk_sha256 decides: equal to the published file -> that build/release; different -> only an older
    receipt stays (for display), never one of the current build. No hash (companion up to v1.4.0, a report stored
    before migration 019, an ADB phone never read) -> the receipts, as before."""
    items, projs = last_installs(device_id)
    for iid, a in arts.items():
        h = a and a['package'] and _reported_hash(present, a['package'])
        if not h:
            continue
        if h == a['sha256']:
            items[iid] = {'build_job_id': a['job_id'], 'version_name': a['version_name'],
                          'version_code': a['version_code'], 'patch_version': a['patches_version']}
        elif items.get(iid, {}).get('build_job_id') == a['job_id']:
            del items[iid]
    for p in infos:
        h = p['package'] and _reported_hash(present, p['package'])
        if not h:
            continue
        if h == p['sha256']:
            projs[p['project_id']] = {'upstream_tag': p['tag']}
        elif projs.get(p['project_id'], {}).get('upstream_tag') == p['tag']:
            del projs[p['project_id']]
    return items, projs


def tracked_packages():
    """Packages the server builds or follows: the only ones a phone report is stored for. Built artifacts count on
    their own: a package-rename patch makes the installed package differ from item.package."""
    return [r[0] for r in db.conn().execute(
        'SELECT package FROM item WHERE package IS NOT NULL UNION SELECT package FROM project WHERE package IS NOT NULL '
        "UNION SELECT package FROM artifacts WHERE package IS NOT NULL AND package != '' ORDER BY 1")]


def store_reported(device_id, packages):
    """Replace a phone's device_packages with the tracked part of packages {package: {version_code, version_name,
    signer_sha256, apk_sha256, apk_path}}; one transaction."""
    keep = set(tracked_packages())
    now = db.utcnow()
    with db.tx() as c:
        c.execute('DELETE FROM device_packages WHERE device_id = ?', (device_id,))
        c.executemany('INSERT INTO device_packages(device_id,package,version_code,version_name,signer_sha256,apk_sha256,'
                      'apk_path,reported_at) VALUES (?,?,?,?,?,?,?,?)',
                      [(device_id, pkg, v['version_code'], v['version_name'], v['signer_sha256'], v['apk_sha256'],
                        v['apk_path'], now)
                       for pkg, v in sorted(packages.items()) if pkg in keep])


def reported_packages(device_id):
    """device_packages of a phone: {package: {version_code, version_name, signer_sha256, apk_sha256, reported_at}}."""
    return {r['package']: {k: r[k] for k in ('version_code', 'version_name', 'signer_sha256', 'apk_sha256',
                                             'reported_at')}
            for r in db.conn().execute('SELECT * FROM device_packages WHERE device_id = ?', (device_id,))}


_STEP_KEYS = ('upstream_id', 'package', 'path', 'file', 'abi', 'tag', 'version_name', 'version_code')


def _project_step(p, action, reason):
    return dict({k: p[k] for k in _STEP_KEYS}, kind='project', ref_id=p['project_id'], action=action, reason=reason)


def _project_blocker(p):
    """Error code that stops installing project info p, or None. One rule for plan time, run time and recovery;
    at plan time NOT_DOWNLOADED is an install step (the ADB runner pulls first, the companion gets a pull)."""
    if p['signer_alert']:
        return 'SIGNER_CHANGED'
    if p['tag'] is None:
        return 'NOT_DOWNLOADED'
    if p['path'] is None:
        return 'NO_MATCHING_ABI'
    return None


def _project_action(p, prev, has):
    code = _project_blocker(p)
    if code == 'NOT_DOWNLOADED':
        return 'install', 'not downloaded'
    if code:
        return 'error', code
    if not has(p['package']):
        return 'install', 'missing on phone'
    if prev is None or prev['upstream_tag'] != p['tag']:
        return 'install', 'new release'
    return 'up_to_date', 'same release installed'


def _drifted(present, pkg, last):
    """Rule 4: the phone reports another versionCode than the recorded install. A record without a versionCode
    (artifact built without one) cannot drift."""
    return present is not None and last['version_code'] is not None and present[pkg]['version_code'] != last['version_code']


def plan(items, artifacts, deps_by_item, direct, item_installs, proj_installs, present):
    """Pure planner (spec follow-project 5.4, android 6.1). present = {package: {version_code, ...}} or None for
    'unknown': every package counts as present and none can drift."""
    def has(pkg):
        return present is None or pkg in present
    steps, planned = [], set()

    def add(p, skip_up_to_date):
        if p['project_id'] in planned:
            return
        action, reason = _project_action(p, proj_installs.get(p['project_id']), has)
        if skip_up_to_date and action == 'up_to_date':
            return
        planned.add(p['project_id'])
        steps.append(_project_step(p, action, reason))
    for it in items:
        iid, a = it['item_id'], artifacts.get(it['item_id'])
        deps = deps_by_item.get(iid, [])
        # error steps name the source item.package: nothing is installed, so no phone package is known
        step = {'kind': 'item', 'ref_id': iid, 'package': it['package'], 'artifact': a,
                'needs': [d['project_id'] for d in deps]}
        if a is None:
            steps.append(dict(step, action='error', reason='NO_MATCHING_ARTIFACT'))
            continue
        if not a['package']:  # not guessed from item.package: a package-rename patch makes them differ
            steps.append(dict(step, action='error', reason=f"ARTIFACT_INCOMPLETE: artifact {a['id']} has no package"))
            continue
        pkg = step['package'] = a['package']  # the phone has the built APK's package, not the source item.package
        last = item_installs.get(iid)
        if not has(pkg):
            action, reason = 'install', 'missing on phone'
        elif last is None or last['build_job_id'] != a['job_id']:
            action, reason = 'install', 'new build'
        elif _drifted(present, pkg, last):
            action, reason = 'install', 'changed on phone'
        else:
            action, reason = 'up_to_date', 'same build installed'
        if action == 'install':
            for d in deps:
                add(d, True)
        steps.append(dict(step, action=action, reason=reason))
    for p in direct:
        add(p, False)
    return steps


SELF_PACKAGE = 'io.github.jhangyu.relume'   # the companion app: the one package a legacy client is offered unasked


def _legacy_scope(item_ids, project_ids):
    """(items, project ids) of a legacy row: ids asked for (any enabled app, any project, assigned or not), else
    only the companion app's own item/project. Ids that do not exist drop out."""
    c = db.conn()
    if item_ids is None and project_ids is None:
        item_ids = [r[0] for r in c.execute('SELECT id FROM item WHERE package = ?', (SELF_PACKAGE,))]
        project_ids = [r[0] for r in c.execute('SELECT id FROM project WHERE package = ?', (SELF_PACKAGE,))]
    items = [dict(r) for r in c.execute(
        'SELECT item.id AS item_id, item.package, COALESCE(item.display_name, store_app.name, item.package) AS display_name '
        'FROM item LEFT JOIN store_app ON store_app.package = item.package WHERE item.enabled = 1 ORDER BY item.id')
        if r['item_id'] in (item_ids or ())]
    return items, [p for p in (project_ids or ()) if c.execute('SELECT 1 FROM project WHERE id = ?', (p,)).fetchone()]


def _plan_for(dev, item_ids, project_ids, present):
    """item_ids/project_ids None = all assigned (a legacy row: the companion app). Returns the planner inputs plus
    'direct' ids and 'steps'."""
    abis = abis_of(dev)
    if devices.is_legacy(dev):
        items, direct = _legacy_scope(item_ids, project_ids)
    else:
        items = assigned_items(dev['id'], item_ids)
        direct = [pid for pid in projects.assigned_projects(dev['id']) if project_ids is None or pid in project_ids]
    info = {}

    def pinfo(pid):
        if pid not in info:
            info[pid] = projects.project_info(pid, abis)
        return info[pid]
    arts = {i['item_id']: build.latest_artifact(i['item_id'], first_abi(dev)) for i in items}
    deps = {i['item_id']: [p for p in map(pinfo, projects.item_projects(i['item_id'])) if p] for i in items}
    direct_info = [p for p in map(pinfo, direct) if p]
    item_inst, proj_inst = install_records(dev['id'], arts, [p for p in info.values() if p], present)
    steps = plan(items, arts, deps, direct_info, item_inst, proj_inst, present)
    return {'items': items, 'arts': arts, 'deps': deps, 'projects': direct_info, 'item_inst': item_inst,
            'proj_inst': proj_inst, 'steps': steps}


def _device_view(dev, states):
    reported = reported_packages(dev['id'])
    # ponytail: an ADB row with no OTA since migration 008 has no report yet: unknown, not 'nothing installed'
    return _view(dev, states, _plan_for(dev, None, None, reported if reported or devices.is_app(dev) else None), reported)


def device_view_by_id(device_id):
    """Device view of a row known only by id (sign-in, token revoke, adopt): app rows, no adb state."""
    return _device_view(devices.get(device_id), {})


def _view(dev, states, v, reported):
    """Device row + assignments + projects from one _plan_for result v."""
    pending = {(s['kind'], s['ref_id']) for s in v['steps'] if s['action'] == 'install'}
    item_pkgs = {s['ref_id']: s['package'] for s in v['steps'] if s['kind'] == 'item'}
    assignments = []
    for i in v['items']:
        a, inst = v['arts'][i['item_id']], v['item_inst'].get(i['item_id'])
        assignments.append({
            'item_id': i['item_id'],
            'installed': inst and {'version_name': inst['version_name'], 'build_job_id': inst['build_job_id'],
                                   'patch_version': inst['patch_version']},
            'available': a and {'version_name': a['version_name'], 'build_job_id': a['job_id']},
            'pending': ('item', i['item_id']) in pending, 'installed_reported': reported.get(item_pkgs[i['item_id']])})
    pmap = {}

    def entry(p):
        pid = p['project_id']
        if pid not in pmap:
            have, avail = (v['proj_inst'].get(pid) or {}).get('upstream_tag'), p['tag']
            pmap[pid] = {
                'project_id': pid, 'display_name': p['display_name'], 'package': p['package'], 'assigned': False,
                'required_by': [], 'installed_tag': have, 'available_tag': avail, 'available_file': p['file'],
                'available_abi': p['abi'],
                'update_available': bool(have and avail and catalog.version_key(avail) > catalog.version_key(have)),
                'pending': ('project', pid) in pending, 'signer_alert': p['signer_alert'],
                'installed_reported': reported.get(p['package'])}
        return pmap[pid]
    for p in v['projects']:
        entry(p)['assigned'] = True
    for iid, ps in v['deps'].items():
        for p in ps:
            entry(p)['required_by'].append(iid)
    return dict(devices.device_json(dev, states), assignments=assignments,
                projects=[pmap[k] for k in sorted(pmap)])


def list_devices(req):
    states = devices.online_states()
    rows = db.conn().execute(f'SELECT * FROM devices WHERE NOT {devices.LEGACY_SQL} ORDER BY id').fetchall()
    return 200, {'devices': [_device_view(dict(r), states) for r in rows]}


class PackageListFailed(Exception):
    """adb could not list the phone's packages: a plan cannot know what is installed."""


PM_TIMEOUT = 60   # seconds for one read-only adb shell call (package list, pm path, sha256sum)


def _adb_out(dev, args, what, cancel_event):
    """Output of one read-only adb shell call; timeout or a non-zero exit -> PackageListFailed prefixed with what."""
    try:
        rc, out = devices.adb(args, serial=devices.address(dev), timeout=PM_TIMEOUT, cancel_event=cancel_event, log=None)
    except devices.AdbTimeout:
        raise PackageListFailed(f'{what}timeout')
    if rc != 0:
        raise PackageListFailed(f'{what}exit {rc}: {devices.last_line(out)}')
    return out


def _present(dev, cancel_event=None):
    """Installed packages read over adb, stored as the phone's report. The base APK sha256 (decision 15) of a tracked
    package is read when its path differs from the stored one; an unchanged path reuses the stored hash.
    Raises PackageListFailed."""
    try:
        present = devices.parse_packages(_adb_out(dev, ['shell', 'pm', 'list', 'packages', '-f', '--show-versioncode'],
                                                  '', cancel_event))
    except ValueError as e:
        raise PackageListFailed(f'pm list packages: {e}') from e
    known = {r['package']: (r['apk_path'], r['apk_sha256']) for r in db.conn().execute(
        'SELECT package, apk_path, apk_sha256 FROM device_packages WHERE device_id = ?', (dev['id'],))}
    for pkg in sorted(set(present) & set(tracked_packages())):
        path = present[pkg]['apk_path']
        old_path, old_hash = known.get(pkg, (None, None))
        if old_hash and old_path == path:
            # ponytail: Android installs every APK into a new random directory, so an unchanged path is an unchanged
            # file. A root user who rewrites base.apk in place keeps the old hash.
            present[pkg]['apk_sha256'] = old_hash
            continue
        what = f'sha256sum {pkg}: '
        if not devices.APK_PATH.fullmatch(path):
            raise PackageListFailed(f'{what}expected an APK path of letters, digits and ._~=+/-, got {path!r}')
        try:
            present[pkg]['apk_sha256'] = devices.parse_sha256sum(
                _adb_out(dev, ['shell', 'sha256sum', path], what, cancel_event))
        except ValueError as e:
            raise PackageListFailed(f'{what}{e}') from e
    store_reported(dev['id'], present)
    return present


# ---- Relume app endpoints (spec android 5, 5.1, 6.3, 6.4); the device is req.device_id (token-only paths) ----

SIGNER = re.compile(r'^[0-9a-f]{64}$')
RECEIPT_RESULTS = ('installed', 'failed')
ERROR_TEXT_MAX = 2048


ARTIFACT_APP_FIELDS = ('size', 'sha256', 'signer_sha256', 'version_code')   # nullable in the schema, required by the app


def _incomplete(row, label):
    """Error reason naming the facts a downloadable file lacks, or None when the app can verify it."""
    missing = [f for f in ARTIFACT_APP_FIELDS if row[f] is None]
    return f"ARTIFACT_INCOMPLETE: {label} has no {', '.join(missing)}" if missing else None


def _item_step(s, name):
    """Plan step of a catalog item. An install whose artifact lacks a field the app needs becomes an error step."""
    step = {'kind': 'item', 'ref_id': s['ref_id'], 'package': s['package'], 'display_name': name,
            'action': s['action'], 'reason': s['reason'], 'needs': s['needs'], 'pull_job_id': None,
            'signer_change': None, 'download': None}
    if s['action'] != 'install':
        return step
    a = s['artifact']
    bad = _incomplete(a, f"artifact {a['id']}")
    if bad:
        return dict(step, action='error', reason=bad)
    return dict(step, download=_item_download(a))


def _item_download(a):
    return {'url': f"/api/artifacts/{a['id']}/download", 'file_name': os.path.basename(a['path']), 'size': a['size'],
            'sha256': a['sha256'], 'signer_sha256': a['signer_sha256'], 'version_code': a['version_code'],
            'version_name': a['version_name'], 'artifact_id': a['id'], 'patch_version': a['patches_version']}


def _project_download(p):
    return {'url': projects.apk_url(p['project_id'], p['file']), 'file_name': p['file'],
            'size': p['size'], 'sha256': p['sha256'], 'signer_sha256': p['signer_sha256'],
            'version_code': p['version_code'], 'version_name': p['version_name'], 'tag': p['tag']}


def _app_project_step(p, action, reason):
    """Plan step of a project. 'not downloaded' becomes an error with a server-enqueued pull
    (jobs.enqueue dedups a still-queued pull, so repeated plans add no job)."""
    step = {'kind': 'project', 'ref_id': p['project_id'], 'package': p['package'], 'display_name': p['display_name'],
            'needs': [], 'pull_job_id': None, 'signer_change': None, 'download': None}
    if reason == 'not downloaded':
        action, reason = 'error', 'NOT_DOWNLOADED'
        step['pull_job_id'] = jobs.enqueue('upstream', {'upstream_id': p['upstream_id']}, 'manual')
    elif reason == 'SIGNER_CHANGED':
        pinned = db.conn().execute('SELECT signer_sha256 FROM project WHERE id = ?', (p['project_id'],)).fetchone()[0]
        step['signer_change'] = {'pinned': pinned, 'offered': p['signer_alert']}
    elif action == 'install':
        bad = _incomplete(p, f"project {p['project_id']}")
        if bad:
            action, reason = 'error', bad
        else:
            step['download'] = _project_download(p)
    return dict(step, action=action, reason=reason)


def _flags(step, present):
    r, d = present.get(step['package']), step['download']
    return dict(step, signer_conflict=bool(r and d and r['signer_sha256'] and r['signer_sha256'] != d['signer_sha256']),
                downgrade=bool(r and d and d['version_code'] is not None and r['version_code'] > d['version_code']))


def _app_plan_from(v, present):
    """{revision, steps} of the companion from one _plan_for result on the phone's report."""
    infos = {p['project_id']: p for p in [*v['projects'], *(p for ps in v['deps'].values() for p in ps)]}
    names = {i['item_id']: i['display_name'] for i in v['items']}
    steps = []
    for s in v['steps']:
        if s['kind'] == 'item':
            steps.append(_item_step(s, names[s['ref_id']]))
        else:
            steps.append(_app_project_step(infos[s['ref_id']], s['action'], s['reason']))
    steps = [_flags(s, present) for s in steps]
    revision = hashlib.sha256(json.dumps(steps, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'revision': revision, 'steps': steps}


def app_plan(dev, item_ids=None, project_ids=None):
    """{revision, steps}: ota.plan() order on the phone's report."""
    present = reported_packages(dev['id'])
    return _app_plan_from(_plan_for(dev, item_ids, project_ids, present), present)


def _me(req):
    return devices.get(req.device_id)


def _me_json(dev):
    return {'device': _device_view(dev, {}), 'tracked_packages': tracked_packages()}


def _sync_json(dev):
    """GET /api/me body plus the full plan, both from one _plan_for (an app row plans on its report)."""
    reported = reported_packages(dev['id'])
    v = _plan_for(dev, None, None, reported)
    return {'device': _view(dev, {}, v, reported), 'tracked_packages': tracked_packages(),
            'plan': _app_plan_from(v, reported)}


def me(req):
    return 200, _me_json(_me(req))


DEVICE_UID_RE = re.compile(r'[0-9A-Za-z]{1,64}\Z')   # Android ANDROID_ID is a 64-bit hex string


def parse_device_uid(device):
    """Boundary rule for device.device_uid, shared by sign-in (auth) and sync. Absent = a companion up to v1.2.0
    (no uid yet) -> None; a present value must be valid."""
    if 'device_uid' not in device:
        return None
    uid = device['device_uid']
    if not isinstance(uid, str) or not DEVICE_UID_RE.match(uid):
        raise HttpError(400, f'device.device_uid must be 1-64 letters or digits, got {uid!r}')
    return uid


def _bad(field, expected, got):
    raise HttpError(400, f'{field} must be {expected}, got {got!r}')


class DeviceFacts(NamedTuple):
    uid: str | None          # None = companion up to v1.2.0 (legacy sign-in, decisions.md entry 1)
    model: str
    sdk: int
    android_release: str
    abi_list: str            # comma-joined, as stored in devices.abi_list


def parse_device_facts(d):
    """Boundary parse of the `device` object shared by sign-in (auth) and sync. Each caller checks its own extra
    field after this: sign-in `name`, sync `app_version_code`."""
    if not isinstance(d, dict):
        _bad('device', 'an object', d)
    uid = parse_device_uid(d)
    for f in ('model', 'android_release'):
        if not isinstance(d.get(f), str) or not d[f].strip():
            _bad(f'device.{f}', 'a non-empty string', d.get(f))
    if not is_int(d.get('sdk')) or d['sdk'] < 1:
        _bad('device.sdk', 'a positive integer', d.get('sdk'))
    abis = d.get('abi_list')
    if not isinstance(abis, list) or not abis:
        _bad('device.abi_list', 'a non-empty list', abis)
    for i, a in enumerate(abis):
        if a not in catalog.ABIS:
            _bad(f'device.abi_list[{i}]', 'one of ' + ', '.join(catalog.ABIS), a)
    return DeviceFacts(uid, d['model'], d['sdk'], d['android_release'], ','.join(abis))


def _parse_sync(body):
    """Boundary parse of POST /api/me/sync -> (DeviceFacts, app_version_code, {package: {version_code, version_name, signer_sha256,
    apk_sha256}}; apk_path is always None, the app reports its own hash). apk_sha256 absent/null = no hash (companions
    up to v1.4.0, decision 15)."""
    d = body.get('device')
    facts = parse_device_facts(d)
    if not is_int(d.get('app_version_code')) or d['app_version_code'] < 1:
        _bad('device.app_version_code', 'a positive integer', d.get('app_version_code'))
    pkgs = body.get('packages')
    if not isinstance(pkgs, list):
        _bad('packages', 'a list', pkgs)
    out = {}
    for i, e in enumerate(pkgs):
        f = f'packages[{i}]'
        if not isinstance(e, dict):
            _bad(f, 'an object', e)
        pkg = e.get('package')
        if not isinstance(pkg, str) or not server.PACKAGE.match(pkg):
            _bad(f + '.package', 'an Android package name', pkg)
        if pkg in out:
            _bad(f + '.package', 'unique in packages', pkg)
        if not is_int(e.get('version_code')) or e['version_code'] < 0:
            _bad(f + '.version_code', 'a non-negative integer', e.get('version_code'))
        if e.get('version_name') is not None and not isinstance(e['version_name'], str):
            _bad(f + '.version_name', 'a string or null', e['version_name'])
        if e.get('signer_sha256') is not None and not (isinstance(e['signer_sha256'], str)
                                                       and SIGNER.match(e['signer_sha256'])):
            _bad(f + '.signer_sha256', '64 lowercase hex characters or null', e['signer_sha256'])
        if e.get('apk_sha256') is not None and not (isinstance(e['apk_sha256'], str) and SIGNER.match(e['apk_sha256'])):
            _bad(f + '.apk_sha256', '64 lowercase hex characters or null', e['apk_sha256'])
        out[pkg] = {'version_code': e['version_code'], 'version_name': e.get('version_name'),
                    'signer_sha256': e.get('signer_sha256'), 'apk_sha256': e.get('apk_sha256'), 'apk_path': None}
    return facts, d['app_version_code'], out


def _leave_legacy(c, device_id, uid):
    """The same token syncs with a uid: the app was replaced by a current one. The legacy row becomes a normal app
    row with that uid, or when a row with that uid exists the token moves there and the legacy row goes. -> row id.
    The row is re-read inside the transaction: a concurrent sync may have converted or merged it already."""
    row = c.execute('SELECT kind, device_uid, token_hash FROM devices WHERE id=?', (device_id,)).fetchone()
    if row is None:
        raise HttpError(401, 'invalid device token')   # merged by a concurrent sync: the token now lives on the owner row
    if not devices.is_legacy(row):
        return device_id
    owner = c.execute('SELECT id FROM devices WHERE device_uid=?', (uid,)).fetchone()
    if owner is None:
        c.execute('UPDATE devices SET device_uid=? WHERE id=?', (uid, device_id))
        return device_id
    c.execute('DELETE FROM devices WHERE id=?', (device_id,))   # token_hash is UNIQUE: free it before the move
    c.execute('UPDATE devices SET token_hash=? WHERE id=?', (row['token_hash'], owner['id']))
    return owner['id']


def sync(req):
    """Report + plan in one call: facts and tracked packages stored, full plan returned. A legacy row (no uid yet)
    stores the report and last_seen only: it gets no device facts."""
    facts, app_version_code, packages = _parse_sync(req.json)
    uid = facts.uid
    did = req.device_id
    me = _me(req)
    if me is None:   # merged into another row by a concurrent sync after authenticate
        raise HttpError(401, 'invalid device token')
    if devices.is_legacy(me):
        if uid is None:
            with db.tx() as c:
                c.execute('UPDATE devices SET last_seen_at=? WHERE id=?', (db.utcnow(), did))
            store_reported(did, packages)
            dev = _me(req)
            return 200, _sync_json(dev)
        with db.tx() as c:
            did = _leave_legacy(c, did, uid)
    with db.tx() as c:
        if uid is not None:   # after _leave_legacy every app row that syncs with a uid has one
            have = c.execute('SELECT device_uid FROM devices WHERE id=?', (did,)).fetchone()['device_uid']
            if have != uid:
                raise HttpError(400, f"device.device_uid must match this device's id {have!r}, got {uid!r}")
        c.execute('UPDATE devices SET model=?, sdk=?, android_release=?, abi_list=?, app_version_code=?, last_seen_at=? '
                  'WHERE id=?', (facts.model, facts.sdk, facts.android_release, facts.abi_list, app_version_code,
                                 db.utcnow(), did))
    store_reported(did, packages)
    dev = devices.get(did)
    return 200, _sync_json(dev)


def _query_ids(req, key, have, what):
    raw = req.query.get(key)
    if raw is None:
        return None
    try:
        ids = [int(x) for x in raw.split(',')]
    except ValueError:
        raise HttpError(400, f'{key} must be comma-separated integers, got {raw!r}')
    if have is not None and not set(ids) <= have:
        raise HttpError(400, f'{what} not assigned to this device')
    return ids


def me_plan(req):
    """?items=&projects= scope the plan like an OTA job target: either key given, the missing one means none."""
    dev = _me(req)
    legacy = devices.is_legacy(dev)   # an install request of a legacy client is answered for any app, assigned or not
    item_ids = _query_ids(req, 'items', None if legacy else assigned_item_ids(dev['id']), 'item')
    project_ids = _query_ids(req, 'projects', None if legacy else set(projects.assigned_projects(dev['id'])), 'project')
    if item_ids is not None or project_ids is not None:
        item_ids, project_ids = item_ids or [], project_ids or []
    return 200, {'plan': app_plan(dev, item_ids, project_ids)}


def _receipt_target(dev, b):
    """-> (build_job_id, upstream_tag, package, version_name, version_code) of the install the receipt is for;
    409 stale plan when the key is not the current download."""
    ref = b.get('ref_id')
    if not is_int(ref):
        _bad('ref_id', 'an integer', ref)
    if b['kind'] == 'item':
        aid = b.get('artifact_id')
        if not is_int(aid):
            _bad('artifact_id', 'an integer', aid)
        item = next((i for i in assigned_items(dev['id']) if i['item_id'] == ref), None)
        if item is None:
            if ref in assigned_item_ids(dev['id']):  # removed in Apps while the phone installed it: the plan changed
                raise HttpError(409, 'stale plan')
            raise HttpError(400, f'item {ref} not assigned to this device')
        a = build.latest_artifact(ref, first_abi(dev))
        if a is None or a['id'] != aid or not a['package']:  # no package = never offered for download
            raise HttpError(409, 'stale plan')
        return a['job_id'], None, a['package'], a['version_name'], a['version_code']
    tag = b.get('tag')
    if not isinstance(tag, str):
        _bad('tag', 'a string', tag)
    p = projects.project_info(ref, abis_of(dev))
    if p is None:
        raise HttpError(400, f'project {ref} not found')
    if p['tag'] != tag or p['path'] is None:
        raise HttpError(409, 'stale plan')
    return None, tag, p['package'], p['version_name'], p['version_code']


def post_install(req):
    """One ota_installs row (job_id NULL) per receipt: history only, the phone's next sync writes its report
    (decision 15)."""
    dev, b = _me(req), req.json
    if b.get('kind') not in ('item', 'project'):
        _bad('kind', 'item or project', b.get('kind'))
    if b.get('result') not in RECEIPT_RESULTS:
        _bad('result', ' or '.join(RECEIPT_RESULTS), b.get('result'))
    code, text = b.get('error_code'), b.get('error_text')
    if code is not None and code not in devices.APP_ERROR_CODES:
        _bad('error_code', 'one of ' + ', '.join(sorted(devices.APP_ERROR_CODES)), code)
    if text is not None and not isinstance(text, str):
        _bad('error_text', 'a string or null', text)
    if devices.is_legacy(dev):   # install-only client: the install happened, nothing about it is kept
        return 200, {'ok': True}
    job_id, tag, pkg, vname, vcode = _receipt_target(dev, b)
    now = db.utcnow()
    with db.tx() as c:
        c.execute('INSERT INTO ota_installs(job_id,device_id,kind,ref_id,build_job_id,upstream_tag,package,version_name,'
                  'version_code,result,error_code,error_text,at) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (dev['id'], b['kind'], b['ref_id'], job_id, tag, pkg, vname, vcode, b['result'], code,
                   text[-ERROR_TEXT_MAX:] if text else None, now))
    return 200, {'ok': True}


def adopt(req):
    """The phone takes over an ADB row: assignments, projects and history stay on that id, the phone's own new row
    goes away (its receipts and report move over first)."""
    tid = req.json.get('device_id')
    if not is_int(tid):
        _bad('device_id', 'an integer', tid)
    me_id = req.device_id
    with db.tx() as c:
        me_row = dict(c.execute('SELECT * FROM devices WHERE id = ?', (me_id,)).fetchone())
        t = c.execute('SELECT kind FROM devices WHERE id = ?', (tid,)).fetchone()
        if t is None:
            raise HttpError(404, f'device {tid} not found')
        if t['kind'] != 'adb':
            raise HttpError(409, 'not an ADB device')
        c.execute('UPDATE ota_installs SET device_id = ? WHERE device_id = ?', (tid, me_id))
        c.execute('DELETE FROM device_packages WHERE device_id = ?', (tid,))
        c.execute('UPDATE device_packages SET device_id = ? WHERE device_id = ?', (tid, me_id))
        c.execute('DELETE FROM devices WHERE id = ?', (me_id,))
        c.execute("UPDATE devices SET kind='app', serial=NULL, mdns_name=NULL, host=NULL, connect_port=NULL, "
                  'ota_after_build=0, device_uid=?, token_hash=?, model=?, abi_list=?, sdk=?, android_release=?, '
                  'app_version_code=?, last_seen_at=? WHERE id=?',
                  (*(me_row[k] for k in ('device_uid', 'token_hash', 'model', 'abi_list', 'sdk', 'android_release',
                                         'app_version_code')), db.utcnow(), tid))
    return 200, {'device': device_view_by_id(tid)}


ROUTES = [
    ('GET', '/api/devices', list_devices),
    ('GET', '/api/me', me),
    ('POST', '/api/me/sync', sync),
    ('GET', '/api/me/plan', me_plan),
    ('POST', '/api/me/installs', post_install),
    ('POST', '/api/me/adopt', adopt),
]

RETRY_WAIT = 60
INSTALL_TIMEOUT = 900
_DONE = ('success', 'failed', 'cancelled')


def _finish(job, device_id, outcome, reason, event, extra):
    with db.tx() as c:
        c.execute('UPDATE ota_jobs SET outcome=? WHERE job_id=?', (outcome, job.id))
        c.execute('UPDATE jobs SET reason=? WHERE id=?', (reason, job.id))
    events.emit(event, dict(extra, job_id=job.id, device_id=device_id))


def _fail(job, device_id, reason, event='ota.failed', extra=None, outcome='failed'):
    _finish(job, device_id, outcome, reason, event, {'reason': reason} if extra is None else extra)
    raise jobs.JobFailed(reason)


def _record(job, device_id, step, result, error_code=None, error_text=None, a=None):
    proj = step['kind'] == 'project'
    with db.tx() as c:
        c.execute('INSERT INTO ota_installs(job_id,device_id,kind,ref_id,build_job_id,upstream_tag,package,version_name,'
                  'version_code,result,error_code,error_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (job.id, device_id, step['kind'], step['ref_id'], a and a['job_id'],
                   step['tag'] if proj else None, step['package'] or '',
                   step['version_name'] if proj else a and a['version_name'],
                   step['version_code'] if proj else a and a['version_code'], result, error_code,
                   error_text[-ERROR_TEXT_MAX:] if error_text else None))
    return result in ('installed', 'up_to_date')


def _install(job, dev, path, package, log):
    """-> (error_code or None, output). Mode force_reinstall uninstalls first (manual only)."""
    serial, mode = devices.address(dev), job.options.get('mode', 'normal')
    try:
        if mode == 'force_reinstall':
            devices.adb(['uninstall', package], serial=serial, timeout=120, cancel_event=job.cancel_event, log=log)
        argv = ['install', '-r'] + (['-d'] if mode == 'downgrade' else []) + [path]
        rc, out = devices.adb(argv, serial=serial, timeout=INSTALL_TIMEOUT, cancel_event=job.cancel_event, log=log)
    except devices.AdbTimeout:
        return ('ADB_ERROR', 'cancelled') if job.cancel_event.is_set() else ('TIMEOUT', 'install timed out')
    code = devices.install_error(out)
    return (code or ('ADB_ERROR' if rc != 0 else None)), out


def _pull_project(job, step, log):
    """A project that was never downloaded is pulled by its upstream job, then installed. False = cancelled."""
    log.line(f"project {step['ref_id']} not downloaded: pulling upstream {step['upstream_id']}")
    jid = jobs.enqueue('upstream', {'upstream_id': step['upstream_id']}, job.trigger,
                       options={'notify': job.options.get('notify', True)})
    cond = jobs.log_condition(jid)  # notified when the upstream job ends and on any cancel (jobs.cancel)
    while True:
        seen = cond.mark()
        row = jobs.get(jid)
        if row is None or row['status'] in _DONE:
            return True
        if job.cancel_event.is_set():
            return False
        cond.wait_change(seen)


REREADS = 2   # retention may delete the file between a read and the install: read once more before failing


def _reread(read, blocker):
    """read() right before an install, again while blocker(row) names a code. -> (row, None) or (None, last code)."""
    for _ in range(REREADS):
        row = read()
        code = blocker(row)
        if code is None:
            return row, None
    return None, code


def _project_install_blocker(p):
    if p is None:
        return 'NOT_DOWNLOADED'
    return _project_blocker(p) or (None if os.path.exists(p['path']) else 'NOT_DOWNLOADED')


def _run_project(job, dev, step, abis, log, failed):
    def fail(code, text):
        failed.add(step['ref_id'])
        return _record(job, dev['id'], step, 'failed', code, text)
    if step['action'] == 'error':
        return fail(step['reason'], step['reason'])
    if step['action'] == 'up_to_date':
        return _record(job, dev['id'], step, 'up_to_date')
    if step['path'] is None and not _pull_project(job, step, log):
        return fail('NOT_DOWNLOADED', 'cancelled')
    p, code = _reread(lambda: projects.project_info(step['ref_id'], abis), _project_install_blocker)
    if code:
        return fail(code, 'project file missing' if code == 'NOT_DOWNLOADED' else code)
    step = _project_step(p, 'install', step['reason'])
    code, out = _install(job, dev, step['path'], step['package'], log)
    if code:
        return fail(code, out)
    return _record(job, dev['id'], step, 'installed')


def _run_item(job, dev, step, abi, log, failed):
    if failed.intersection(step['needs']):
        return _record(job, dev['id'], step, 'failed', 'DEPENDENCY_FAILED', 'dependency install failed', step['artifact'])
    if step['action'] == 'error':
        return _record(job, dev['id'], step, 'failed', step['reason'].split(':')[0], step['reason'])
    if step['action'] == 'up_to_date':
        return _record(job, dev['id'], step, 'up_to_date', a=step['artifact'])
    # ponytail: no fd pinning. A file deleted by retention between this read and adb's open fails ADB_ERROR and
    # the next OTA retries.
    a, code = _reread(lambda: build.latest_artifact(step['ref_id'], abi),
                      lambda a: None if a and os.path.exists(a['path']) else 'NOT_DOWNLOADED')
    if code:
        return _record(job, dev['id'], step, 'failed', code, 'artifact file missing')
    code, out = _install(job, dev, a['path'], step['package'], log)
    if code:
        return _record(job, dev['id'], step, 'failed', code, out, a)
    return _record(job, dev['id'], step, 'installed', a=a)


def _recovery_steps(dev, package, abis):
    """Assigned items, then directly assigned projects, then dependency projects of assigned items."""
    items = assigned_items(dev['id'])
    for i in items:  # by the built package: the one ota_installs records and the phone has
        a = build.latest_artifact(i['item_id'], first_abi(dev))
        if a and a['package'] == package:
            return [{'kind': 'item', 'ref_id': i['item_id'], 'package': package, 'artifact': a, 'needs': [],
                     'action': 'install', 'reason': 'recovery'}]
    pids = projects.assigned_projects(dev['id']) + [pid for i in items for pid in projects.item_projects(i['item_id'])]
    for pid in dict.fromkeys(pids):
        p = projects.project_info(pid, abis)
        if p and p['package'] == package:
            code = _project_blocker(p)
            return [_project_step(p, 'error', code) if code else _project_step(p, 'install', 'recovery')]
    return None


def _reconnect(dev, job, log, attempts):
    """devices.reconnect with the approved offline retry (decision 8): RETRY_WAIT s between tries, cancellable.
    -> the last state, or None when the job was cancelled."""
    for n in range(attempts):
        state = devices.reconnect(dev, cancel_event=job.cancel_event, log=log)
        if state != 'offline' or n == attempts - 1:
            break
        log.line(f'device offline, retry {n + 1} of {attempts - 1} in {RETRY_WAIT} s')
        if job.cancel_event.wait(RETRY_WAIT):
            return None
    return None if job.cancel_event.is_set() else state


def handle(job, log):
    mode = job.options.get('mode', 'normal')
    if mode != 'normal' and job.trigger != 'manual':
        raise jobs.JobFailed('recovery requires manual trigger')
    dev = devices.get(job.target.get('device_id'))
    if dev is None:
        raise jobs.JobFailed('device removed')
    if devices.is_app(dev):
        raise jobs.JobFailed(devices.APP_ONLY)
    item_ids, project_ids = job.target.get('item_ids'), job.target.get('project_ids')
    if item_ids is not None or project_ids is not None:  # either key scopes the job: a missing key means none
        item_ids, project_ids = item_ids or [], project_ids or []
    with db.tx() as c:
        c.execute('INSERT OR REPLACE INTO ota_jobs(job_id,device_id,item_ids,project_ids,mode,outcome) '
                  'VALUES (?,?,?,?,?,NULL)', (job.id, dev['id'], None if item_ids is None else json.dumps(item_ids),
                                              None if project_ids is None else json.dumps(project_ids), mode))
    abis = abis_of(dev)
    recovery = None
    if mode != 'normal':
        recovery = _recovery_steps(dev, job.options.get('package'), abis)
        if recovery is None:
            _fail(job, dev['id'], 'package not assigned')
    attempts = 1 + (int(job.options.get('retries') or 0) if job.trigger in ('schedule', 'after_build') else 0)
    state = _reconnect(dev, job, log, attempts)
    if state is None:
        return
    if state == 'unauthorized':
        _fail(job, dev['id'], 'failed: UNAUTHORIZED', extra={'reason': 'UNAUTHORIZED'})
    if state == 'offline':
        if job.trigger == 'manual':
            _fail(job, dev['id'], 'failed: DEVICE_OFFLINE', extra={'reason': 'DEVICE_OFFLINE'})
        _fail(job, dev['id'], 'skipped_offline', event='device.offline', extra={}, outcome='skipped_offline')
    steps = recovery
    if steps is None:
        try:
            present = _present(dev, job.cancel_event)
        except PackageListFailed as e:
            if job.cancel_event.is_set():
                return
            _fail(job, dev['id'], f'adb package list failed: {e}')
        steps = _plan_for(dev, item_ids, project_ids, present)['steps']
    ok, failed = [], set()
    for step in steps:
        if job.cancel_event.is_set():
            break
        log.line(f"{step['kind']} {step['package']}: {step['action']} ({step['reason']})")
        if step['kind'] == 'project':
            ok.append(_run_project(job, dev, step, abis, log, failed))
        else:
            ok.append(_run_item(job, dev, step, first_abi(dev), log, failed))
    try:
        _present(dev)  # store what the phone really has now: an installed APK has a new path and is hashed again, unchanged ones reuse the stored hash
    except PackageListFailed as e:
        log.line(f'adb package list failed after the installs: {e}, the stored report keeps the pre-run list')
    if job.cancel_event.is_set():
        return
    outcome = 'done' if all(ok) else 'partial' if any(ok) else 'failed'
    if outcome == 'failed':
        _fail(job, dev['id'], 'failed')
    _finish(job, dev['id'], outcome, outcome, 'ota.done', {'outcome': outcome})


def detail(job_id):
    c = db.conn()
    r = c.execute('SELECT * FROM ota_jobs WHERE job_id=?', (job_id,)).fetchone()
    if r is None:
        return {}
    return {'device_id': r['device_id'], 'item_ids': json.loads(r['item_ids']) if r['item_ids'] else None,
            'project_ids': json.loads(r['project_ids']) if r['project_ids'] else None,
            'mode': r['mode'], 'outcome': r['outcome'],
            'installs': [dict(x) for x in c.execute('SELECT * FROM ota_installs WHERE job_id=? ORDER BY id', (job_id,))]}


def on_build_succeeded(payload):
    item_id = payload.get('item_id')
    rows = db.conn().execute('SELECT d.id FROM devices d JOIN device_assignments da ON da.device_id = d.id '
                             "JOIN item ON item.id = da.item_id WHERE d.kind = 'adb' AND d.ota_after_build = 1 AND item.enabled = 1 "
                             "AND da.item_id = ? ORDER BY d.id", (item_id,)).fetchall()
    for r in rows:
        jobs.enqueue('ota', {'device_id': r['id'], 'item_ids': [item_id]}, 'after_build')


def ota_now(req):
    dev = devices.dev_or_404(req.params['id'])
    if devices.is_app(dev):
        raise HttpError(409, devices.APP_ONLY)
    body, target = req.json or {}, {'device_id': dev['id']}
    for key, what, have in (('item_ids', 'item', lambda: assigned_item_ids(dev['id'])),
                            ('project_ids', 'project', lambda: set(projects.assigned_projects(dev['id'])))):
        ids = body.get(key)
        if ids is None:
            continue
        if not ids:
            raise HttpError(400, f'{key} must be a non-empty list of integers, got {ids!r}')
        ids = list(dict.fromkeys(server.ints(ids, key)))
        if not set(ids) <= have():
            raise HttpError(400, f'{what} not assigned to this device')
        target[key] = ids
    return 200, {'job_id': jobs.enqueue('ota', target, 'manual')}


def install_project(req):
    """POST /api/projects/{id}/install: one manual OTA job per phone, scoped to this project."""
    pid = req.params['id']
    p = projects.project_info(pid, [])
    if p is None:
        raise HttpError(404, 'project not found')
    if p['signer_alert']:
        raise HttpError(409, 'signer changed: confirm on the Followed projects page')
    assigned = projects.project_devices(pid)
    ids = (req.json or {}).get('device_ids')
    if ids is None:
        if not assigned:
            raise HttpError(400, 'project not assigned to any phone')
        ids = assigned
    elif not ids:
        raise HttpError(400, f'device_ids must be a non-empty list of integers, got {ids!r}')
    elif any(d is not None and devices.is_app(d) for d in map(devices.get, server.ints(ids, 'device_ids'))):
        raise HttpError(409, devices.APP_ONLY)
    elif not set(ids) <= set(assigned):
        raise HttpError(400, 'device not assigned')
    return 200, {'job_ids': [jobs.enqueue('ota', {'device_id': d, 'project_ids': [pid]}, 'manual')
                             for d in dict.fromkeys(ids)]}


def _recovery(req, mode):
    install_id = (req.json or {}).get('install_id')
    if not server.is_int(install_id):
        raise HttpError(400, 'install_id must be an integer')
    r = db.conn().execute('SELECT device_id, package FROM ota_installs WHERE id=? AND job_id=?',
                          (install_id, req.params['job_id'])).fetchone()
    if r is None:
        raise HttpError(404, 'install not found')
    dev = devices.get(r['device_id'])
    if dev is not None and devices.is_app(dev):
        raise HttpError(409, devices.APP_ONLY)
    jid = jobs.enqueue('ota', {'device_id': r['device_id']}, 'manual', options={'mode': mode, 'package': r['package']})
    return 200, {'job_id': jid}


def force_reinstall(req):
    return _recovery(req, 'force_reinstall')


def downgrade(req):
    return _recovery(req, 'downgrade')


def register():
    jobs.register('ota', handle, detail)
    events.subscribe('build.succeeded', on_build_succeeded)


ROUTES += [
    ('POST', '/api/devices/{id}/ota', ota_now),
    ('POST', '/api/projects/{id}/install', install_project),
    ('POST', '/api/ota/{job_id}/force-reinstall', force_reinstall),
    ('POST', '/api/ota/{job_id}/downgrade', downgrade),
]
