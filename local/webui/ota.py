"""OTA part: planner, read model, OTA job handler, recovery routes, after-build OTA (spec device 3-4,
spec follow-project 5.4)."""
import hashlib
import json
import os
import re
import urllib.parse

from . import build, catalog, db, devices, events, jobs, projects, server
from .server import HttpError, is_int


def abis_of(dev):
    return [a.strip() for a in (dev.get('abi_list') or '').split(',') if a.strip()] or ['universal']


def first_abi(dev):
    return abis_of(dev)[0]


def assigned_items(device_id, item_ids=None):
    """Assigned items in catalog order (groups by position, Ungrouped last); disabled items included."""
    rows = db.conn().execute(
        "SELECT item.id AS item_id, item.package, COALESCE(item.display_name, "
        "COALESCE(app.display_name, item.package) || ' · ' || source.name) AS display_name "
        'FROM device_assignments da JOIN item ON item.id = da.item_id JOIN source ON source.id = item.source_id '
        'LEFT JOIN app ON app.package = item.package LEFT JOIN grp ON grp.id = item.group_id WHERE da.device_id = ? '
        'ORDER BY grp.position IS NULL, grp.position, item.position, item.id', (device_id,)).fetchall()
    items = [dict(r) for r in rows]
    if item_ids is not None:
        wanted = set(item_ids)
        items = [i for i in items if i['item_id'] in wanted]
    return items


def last_installs(device_id):
    """-> (items, projects): newest installed/up_to_date row per item id and per project id. No JOIN on jobs:
    rows written from an app receipt have job_id NULL."""
    items, projects = {}, {}
    rows = db.conn().execute(
        "SELECT kind, ref_id, build_job_id, upstream_tag, version_name, version_code, at FROM ota_installs "
        "WHERE device_id = ? AND result IN ('installed','up_to_date') ORDER BY id", (device_id,))
    for r in rows:  # oldest first: newer rows overwrite
        if r['kind'] == 'item':
            items[r['ref_id']] = {'build_job_id': r['build_job_id'], 'version_name': r['version_name'],
                                  'version_code': r['version_code'], 'at': r['at']}
        else:
            projects[r['ref_id']] = {'upstream_tag': r['upstream_tag'], 'at': r['at']}
    return items, projects


def tracked_packages():
    """Packages the server builds or follows: the only ones a phone report is stored for."""
    return [r[0] for r in db.conn().execute(
        'SELECT package FROM item WHERE package IS NOT NULL UNION SELECT package FROM project WHERE package IS NOT NULL '
        'ORDER BY 1')]


def store_reported(device_id, packages):
    """Replace a phone's device_packages with the tracked part of packages {package: {version_code, version_name,
    signer_sha256}}; one transaction."""
    keep = set(tracked_packages())
    now = db.utcnow()
    with db.tx() as c:
        c.execute('DELETE FROM device_packages WHERE device_id = ?', (device_id,))
        c.executemany('INSERT INTO device_packages(device_id,package,version_code,version_name,signer_sha256,reported_at) '
                      'VALUES (?,?,?,?,?,?)',
                      [(device_id, pkg, v['version_code'], v['version_name'], v['signer_sha256'], now)
                       for pkg, v in sorted(packages.items()) if pkg in keep])


def reported_packages(device_id):
    """device_packages of a phone: {package: {version_code, version_name, signer_sha256, reported_at}}."""
    return {r['package']: {k: r[k] for k in ('version_code', 'version_name', 'signer_sha256', 'reported_at')}
            for r in db.conn().execute('SELECT * FROM device_packages WHERE device_id = ?', (device_id,))}


_STEP_KEYS = ('upstream_id', 'package', 'path', 'file', 'abi', 'tag', 'version_name', 'version_code')


def _project_step(p, action, reason):
    return dict({k: p[k] for k in _STEP_KEYS}, kind='project', ref_id=p['project_id'], action=action, reason=reason)


def _project_action(p, prev, has):
    if p['signer_alert']:
        return 'error', 'SIGNER_CHANGED'
    if p['tag'] is None:
        return 'install', 'not downloaded'
    if p['path'] is None:
        return 'error', 'NO_MATCHING_ABI'
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
        iid, pkg, a = it['item_id'], it['package'], artifacts.get(it['item_id'])
        deps = deps_by_item.get(iid, [])
        step = {'kind': 'item', 'ref_id': iid, 'package': pkg, 'artifact': a, 'needs': [d['project_id'] for d in deps]}
        if a is None:
            steps.append(dict(step, action='error', reason='NO_MATCHING_ARTIFACT'))
            continue
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


def _plan_for(dev, item_ids, project_ids, present):
    """item_ids/project_ids None = all assigned. Returns the planner inputs plus 'direct' ids and 'steps'."""
    abis = abis_of(dev)
    items = assigned_items(dev['id'], item_ids)
    item_inst, proj_inst = last_installs(dev['id'])
    info = {}

    def pinfo(pid):
        if pid not in info:
            info[pid] = projects.project_info(pid, abis)
        return info[pid]
    arts = {i['item_id']: build.latest_artifact(i['item_id'], first_abi(dev)) for i in items}
    deps = {i['item_id']: [p for p in map(pinfo, projects.item_projects(i['item_id'])) if p] for i in items}
    direct = [pid for pid in projects.assigned_projects(dev['id']) if project_ids is None or pid in project_ids]
    direct_info = [p for p in map(pinfo, direct) if p]
    steps = plan(items, arts, deps, direct_info, item_inst, proj_inst, present)
    return {'items': items, 'arts': arts, 'deps': deps, 'projects': direct_info, 'item_inst': item_inst,
            'proj_inst': proj_inst, 'steps': steps}


def _device_view(dev, states):
    reported = reported_packages(dev['id'])
    # ponytail: an ADB row with no OTA since migration 008 has no report yet: unknown, not 'nothing installed'
    v = _plan_for(dev, None, None, reported if reported or devices.is_app(dev) else None)
    pending = {(s['kind'], s['ref_id']) for s in v['steps'] if s['action'] == 'install'}
    assignments = []
    for i in v['items']:
        a, inst = v['arts'][i['item_id']], v['item_inst'].get(i['item_id'])
        assignments.append({
            'item_id': i['item_id'],
            'installed': inst and {'version_name': inst['version_name'], 'build_job_id': inst['build_job_id'],
                                   'at': inst['at']},
            'available': a and {'version_name': a['version_name'], 'build_job_id': a['job_id']},
            'pending': ('item', i['item_id']) in pending, 'installed_reported': reported.get(i['package'])})
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
    rows = db.conn().execute('SELECT * FROM devices ORDER BY id').fetchall()
    return 200, {'devices': [_device_view(dict(r), states) for r in rows]}


def _present(dev, cancel_event=None):
    """Installed packages read over adb and stored as the phone's report; None = unknown (adb failed)."""
    try:
        rc, out = devices.adb(['shell', 'pm', 'list', 'packages', '--show-versioncode'], serial=devices.address(dev),
                              timeout=60, cancel_event=cancel_event, log=None)
    except devices.AdbTimeout:
        return None
    if rc != 0:
        return None
    present = devices.parse_packages(out)
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
            'version_name': a['version_name'], 'artifact_id': a['id']}


def _project_download(p):
    return {'url': f"/api/projects/{p['project_id']}/apks/{urllib.parse.quote(p['file'])}", 'file_name': p['file'],
            'size': p['size'], 'sha256': p['sha256'], 'signer_sha256': p['signer_sha256'],
            'version_code': p['version_code'], 'version_name': p['version_name'], 'tag': p['tag']}


def _app_project_step(kind, p, action, reason):
    """Plan step of a project or the companion. 'not downloaded' becomes an error with a server-enqueued pull
    (jobs.enqueue dedups a still-queued pull, so repeated plans add no job)."""
    step = {'kind': kind, 'ref_id': p['project_id'], 'package': p['package'], 'display_name': p['display_name'],
            'needs': [], 'pull_job_id': None, 'signer_change': None, 'download': None}
    if reason == 'not downloaded':
        action, reason = 'error', 'NOT_DOWNLOADED'
        step['pull_job_id'] = jobs.enqueue('upstream', {'upstream_id': p['upstream_id']}, 'manual',
                                           options={'rebuild': False})
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


def _companion_action(p, dev):
    if p['signer_alert']:
        return 'error', 'SIGNER_CHANGED'
    if p['tag'] is None:
        return 'install', 'not downloaded'
    if p['path'] is None:
        return 'error', 'NO_MATCHING_ABI'
    bad = _incomplete(p, f"project {p['project_id']}")
    if bad:
        return 'error', bad
    if dev['app_version_code'] is None:
        return 'up_to_date', 'app version not reported'
    if p['version_code'] > dev['app_version_code']:
        return 'install', 'new release'
    return 'up_to_date', 'same version installed'


def _flags(step, present):
    r, d = present.get(step['package']), step['download']
    return dict(step, signer_conflict=bool(r and d and r['signer_sha256'] and r['signer_sha256'] != d['signer_sha256']),
                downgrade=bool(r and d and d['version_code'] is not None and r['version_code'] > d['version_code']))


def app_plan(dev, item_ids=None, project_ids=None):
    """{revision, steps}: ota.plan() order on the phone's report, then the companion step last."""
    present = reported_packages(dev['id'])
    v = _plan_for(dev, item_ids, project_ids, present)
    infos = {p['project_id']: p for p in [*v['projects'], *(p for ps in v['deps'].values() for p in ps)]}
    names = {i['item_id']: i['display_name'] for i in v['items']}
    steps = []
    for s in v['steps']:
        if s['kind'] == 'item':
            steps.append(_item_step(s, names[s['ref_id']]))
        elif s['package'] != projects.COMPANION_PACKAGE:
            steps.append(_app_project_step('project', infos[s['ref_id']], s['action'], s['reason']))
    comp = projects.companion_info(abis_of(dev))
    if comp is not None:
        steps.append(_app_project_step('companion', comp, *_companion_action(comp, dev)))
    steps = [_flags(s, present) for s in steps]
    revision = hashlib.sha256(json.dumps(steps, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'revision': revision, 'steps': steps}


def _me(req):
    return devices.get(req.device_id)


def _me_json(dev):
    return {'device': _device_view(dev, {}), 'tracked_packages': tracked_packages()}


def me(req):
    return 200, _me_json(_me(req))


def _bad(field, expected, got):
    raise HttpError(400, f'{field} must be {expected}, got {got!r}')


def _parse_sync(body):
    """Boundary parse of POST /api/me/sync -> (facts tuple, {package: {version_code, version_name, signer_sha256}})."""
    d = body.get('device')
    if not isinstance(d, dict):
        _bad('device', 'an object', d)
    for f in ('model', 'android_release'):
        if not isinstance(d.get(f), str) or not d[f].strip():
            _bad(f'device.{f}', 'a non-empty string', d.get(f))
    for f in ('sdk', 'app_version_code'):
        if not is_int(d.get(f)) or d[f] < 1:
            _bad(f'device.{f}', 'a positive integer', d.get(f))
    abis = d.get('abi_list')
    if not isinstance(abis, list) or not abis:
        _bad('device.abi_list', 'a non-empty list', abis)
    for i, a in enumerate(abis):
        if a not in catalog.ABIS:
            _bad(f'device.abi_list[{i}]', 'one of ' + ', '.join(catalog.ABIS), a)
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
        out[pkg] = {'version_code': e['version_code'], 'version_name': e.get('version_name'),
                    'signer_sha256': e.get('signer_sha256')}
    return (d['model'], d['sdk'], d['android_release'], ','.join(abis), d['app_version_code']), out


def sync(req):
    """Report + plan in one call: facts and tracked packages stored, full plan returned."""
    facts, packages = _parse_sync(req.json)
    with db.tx() as c:
        c.execute('UPDATE devices SET model=?, sdk=?, android_release=?, abi_list=?, app_version_code=?, last_seen_at=? '
                  'WHERE id=?', (*facts, db.utcnow(), req.device_id))
    store_reported(req.device_id, packages)
    dev = _me(req)
    return 200, dict(_me_json(dev), plan=app_plan(dev))


def _query_ids(req, key, have, what):
    raw = req.query.get(key)
    if raw is None:
        return None
    try:
        ids = [int(x) for x in raw.split(',')]
    except ValueError:
        raise HttpError(400, f'{key} must be comma-separated integers, got {raw!r}')
    if not set(ids) <= have:
        raise HttpError(400, f'{what} not assigned to this device')
    return ids


def me_plan(req):
    """?items=&projects= scope the plan like an OTA job target: either key given, the missing one means none."""
    dev = _me(req)
    item_ids = _query_ids(req, 'items', {i['item_id'] for i in assigned_items(dev['id'])}, 'item')
    project_ids = _query_ids(req, 'projects', set(projects.assigned_projects(dev['id'])), 'project')
    if item_ids is not None or project_ids is not None:
        item_ids, project_ids = item_ids or [], project_ids or []
    return 200, {'plan': app_plan(dev, item_ids, project_ids)}


def _receipt_target(dev, b):
    """-> (build_job_id, upstream_tag, package, version_name, version_code, signer_sha256) of the install the receipt
    is for; 409 stale plan when the key is not the current download."""
    ref = b.get('ref_id')
    if not is_int(ref):
        _bad('ref_id', 'an integer', ref)
    if b['kind'] == 'item':
        aid = b.get('artifact_id')
        if not is_int(aid):
            _bad('artifact_id', 'an integer', aid)
        item = next((i for i in assigned_items(dev['id']) if i['item_id'] == ref), None)
        if item is None:
            raise HttpError(400, f'item {ref} not assigned to this device')
        a = build.latest_artifact(ref, first_abi(dev))
        if a is None or a['id'] != aid:
            raise HttpError(409, 'stale plan')
        return a['job_id'], None, item['package'], a['version_name'], a['version_code'], a['signer_sha256']
    tag = b.get('tag')
    if not isinstance(tag, str):
        _bad('tag', 'a string', tag)
    p = projects.project_info(ref, abis_of(dev))
    if p is None:
        raise HttpError(400, f'project {ref} not found')
    if p['tag'] != tag or p['path'] is None:
        raise HttpError(409, 'stale plan')
    return None, tag, p['package'], p['version_name'], p['version_code'], p['signer_sha256']


def post_install(req):
    """One ota_installs row (job_id NULL) per receipt; an installed receipt also updates the phone's report."""
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
    job_id, tag, pkg, vname, vcode, signer = _receipt_target(dev, b)
    now = db.utcnow()
    with db.tx() as c:
        c.execute('INSERT INTO ota_installs(job_id,device_id,kind,ref_id,build_job_id,upstream_tag,package,version_name,'
                  'version_code,result,error_code,error_text,at) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (dev['id'], b['kind'], b['ref_id'], job_id, tag, pkg, vname, vcode, b['result'], code,
                   text[-ERROR_TEXT_MAX:] if text else None, now))
        if b['result'] == 'installed':
            c.execute('INSERT OR REPLACE INTO device_packages(device_id,package,version_code,version_name,signer_sha256,'
                      'reported_at) VALUES (?,?,?,?,?,?)', (dev['id'], pkg, vcode, vname, signer, now))
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
                  'ota_after_build=0, token_hash=?, model=?, abi_list=?, sdk=?, android_release=?, app_version_code=?, '
                  'last_seen_at=? WHERE id=?',
                  (*(me_row[k] for k in ('token_hash', 'model', 'abi_list', 'sdk', 'android_release',
                                         'app_version_code')), db.utcnow(), tid))
    return 200, {'device': _device_view(devices.get(tid), {})}


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
                       options={'rebuild': False, 'notify': job.options.get('notify', True)})
    while True:
        row = jobs.get(jid)
        if row is None or row['status'] in _DONE:
            return True
        if job.cancel_event.wait(1):
            return False


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
    # re-resolve right before install, once more if the file is gone (retention may prune it), like _run_item
    for _ in range(2):
        p = projects.project_info(step['ref_id'], abis)
        if p is None or p['tag'] is None:
            code = 'NOT_DOWNLOADED'
        elif p['signer_alert']:
            code = 'SIGNER_CHANGED'
        elif p['path'] is None:
            code = 'NO_MATCHING_ABI'
        elif not os.path.exists(p['path']):
            code = 'NOT_DOWNLOADED'
        else:
            code = None
            break
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
        return _record(job, dev['id'], step, 'failed', 'NO_MATCHING_ARTIFACT', 'NO_MATCHING_ARTIFACT')
    if step['action'] == 'up_to_date':
        return _record(job, dev['id'], step, 'up_to_date', a=step['artifact'])
    # ponytail: re-resolve right before install, once more if the file is gone; no fd pinning. A file deleted
    # by retention between this check and adb's open fails ADB_ERROR and the next OTA retries.
    a = None
    for _ in range(2):
        a = build.latest_artifact(step['ref_id'], abi)
        if a and os.path.exists(a['path']):
            break
        a = None
    if a is None:
        return _record(job, dev['id'], step, 'failed', 'NOT_DOWNLOADED', 'artifact file missing')
    code, out = _install(job, dev, a['path'], step['package'], log)
    if code:
        return _record(job, dev['id'], step, 'failed', code, out, a)
    return _record(job, dev['id'], step, 'installed', a=a)


def _recovery_steps(dev, package, abis):
    """Assigned items, then directly assigned projects, then dependency projects of assigned items."""
    items = assigned_items(dev['id'])
    for i in items:
        if i['package'] == package:
            a = build.latest_artifact(i['item_id'], first_abi(dev))
            return [{'kind': 'item', 'ref_id': i['item_id'], 'package': package, 'artifact': a, 'needs': [],
                     'action': 'install' if a else 'error', 'reason': 'recovery' if a else 'NO_MATCHING_ARTIFACT'}]
    pids = projects.assigned_projects(dev['id']) + [pid for i in items for pid in projects.item_projects(i['item_id'])]
    for pid in dict.fromkeys(pids):
        p = projects.project_info(pid, abis)
        if p and p['package'] == package:
            return [_project_step(p, 'install', 'recovery') if p['path'] else
                    _project_step(p, 'error', 'NO_MATCHING_ABI')]
    return None


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
    for n in range(attempts):
        state = devices.reconnect(dev, cancel_event=job.cancel_event, log=log)
        if state != 'offline' or n == attempts - 1:
            break
        log.line(f'device offline, retry {n + 1} of {attempts - 1} in {RETRY_WAIT} s')
        if job.cancel_event.wait(RETRY_WAIT):
            return
    if job.cancel_event.is_set():
        return
    if state == 'unauthorized':
        _fail(job, dev['id'], 'failed: UNAUTHORIZED', extra={'reason': 'UNAUTHORIZED'})
    if state == 'offline':
        if job.trigger == 'manual':
            _fail(job, dev['id'], 'failed: DEVICE_OFFLINE', extra={'reason': 'DEVICE_OFFLINE'})
        _fail(job, dev['id'], 'skipped_offline', event='device.offline', extra={}, outcome='skipped_offline')
    steps = recovery or _plan_for(dev, item_ids, project_ids, _present(dev, job.cancel_event))['steps']
    ok, failed = [], set()
    for step in steps:
        if job.cancel_event.is_set():
            return
        log.line(f"{step['kind']} {step['package']}: {step['action']} ({step['reason']})")
        if step['kind'] == 'project':
            ok.append(_run_project(job, dev, step, abis, log, failed))
        else:
            ok.append(_run_item(job, dev, step, first_abi(dev), log, failed))
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
                             "WHERE d.kind = 'adb' AND d.ota_after_build = 1 AND da.item_id = ? ORDER BY d.id", (item_id,)).fetchall()
    for r in rows:
        jobs.enqueue('ota', {'device_id': r['id'], 'item_ids': [item_id]}, 'after_build')


def _int_list(v):
    return isinstance(v, list) and all(map(server.is_int, v))


def ota_now(req):
    dev = devices.get(req.params['id'])
    if dev is None:
        raise HttpError(404, 'device not found')
    if devices.is_app(dev):
        raise HttpError(409, devices.APP_ONLY)
    body, target = req.json or {}, {'device_id': dev['id']}
    for key, what, have in (('item_ids', 'item', lambda: {i['item_id'] for i in assigned_items(dev['id'])}),
                            ('project_ids', 'project', lambda: set(projects.assigned_projects(dev['id'])))):
        ids = body.get(key)
        if ids is None:
            continue
        if not ids or not _int_list(ids):
            raise HttpError(400, f'{key} must be a non-empty list of integers')
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
    elif not ids or not _int_list(ids):
        raise HttpError(400, 'device_ids must be a non-empty list of integers')
    elif any(d is not None and devices.is_app(d) for d in map(devices.get, ids)):
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
