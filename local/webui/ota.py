"""OTA part: planner, read model, OTA job handler, recovery routes, after-build OTA (spec device 3-4,
spec follow-project 5.4)."""
import json
import os

from . import build, catalog, db, devices, events, jobs, projects
from .server import HttpError


def abis_of(dev):
    return [a.strip() for a in (dev.get('abi_list') or '').split(',') if a.strip()] or ['universal']


def first_abi(dev):
    return abis_of(dev)[0]


def assigned_items(device_id, item_ids=None):
    """Assigned items in catalog order (groups by position, Ungrouped last); disabled items included."""
    rows = db.conn().execute(
        'SELECT item.id AS item_id, item.package FROM device_assignments da JOIN item ON item.id = da.item_id '
        'LEFT JOIN grp ON grp.id = item.group_id WHERE da.device_id = ? '
        'ORDER BY grp.position IS NULL, grp.position, item.position, item.id', (device_id,)).fetchall()
    items = [dict(r) for r in rows]
    if item_ids is not None:
        wanted = set(item_ids)
        items = [i for i in items if i['item_id'] in wanted]
    return items


def last_installs(device_id):
    """-> (items, projects): newest installed/up_to_date row per item id and per project id."""
    items, projects = {}, {}
    rows = db.conn().execute(
        "SELECT oi.kind, oi.ref_id, oi.build_job_id, oi.upstream_tag, oi.version_name, j.finished_at "
        "FROM ota_installs oi JOIN jobs j ON j.id = oi.job_id "
        "WHERE oi.device_id = ? AND oi.result IN ('installed','up_to_date') ORDER BY oi.id", (device_id,))
    for r in rows:  # oldest first: newer rows overwrite
        if r['kind'] == 'item':
            items[r['ref_id']] = {'build_job_id': r['build_job_id'], 'version_name': r['version_name'],
                                  'at': r['finished_at']}
        else:
            projects[r['ref_id']] = {'upstream_tag': r['upstream_tag'], 'at': r['finished_at']}
    return items, projects


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


def plan(items, artifacts, deps_by_item, projects, item_installs, proj_installs, present):
    """Pure planner (spec follow-project 5.4). present=None means 'unknown': every package counts as present."""
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
        else:
            action, reason = 'up_to_date', 'same build installed'
        if action == 'install':
            for d in deps:
                add(d, True)
        steps.append(dict(step, action=action, reason=reason))
    for p in projects:
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
    v = _plan_for(dev, None, None, None)
    pending = {(s['kind'], s['ref_id']) for s in v['steps'] if s['action'] == 'install'}
    assignments = []
    for i in v['items']:
        a, inst = v['arts'][i['item_id']], v['item_inst'].get(i['item_id'])
        assignments.append({
            'item_id': i['item_id'],
            'installed': inst and {'version_name': inst['version_name'], 'build_job_id': inst['build_job_id'],
                                   'at': inst['at']},
            'available': a and {'version_name': a['version_name'], 'build_job_id': a['job_id']},
            'pending': ('item', i['item_id']) in pending})
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
                'pending': ('project', pid) in pending, 'signer_alert': p['signer_alert']}
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


def _present(dev, cancel_event=None, log=None):
    try:
        rc, out = devices.adb(['shell', 'pm', 'list', 'packages'], serial=devices.address(dev), timeout=60,
                              cancel_event=cancel_event, log=None)
    except devices.AdbTimeout:
        return None
    return devices.parse_packages(out) if rc == 0 else None


ROUTES = [
    ('GET', '/api/devices', list_devices),
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
                   error_text[-2048:] if error_text else None))
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
                             'WHERE d.ota_after_build = 1 AND da.item_id = ? ORDER BY d.id', (item_id,)).fetchall()
    for r in rows:
        jobs.enqueue('ota', {'device_id': r['id'], 'item_ids': [item_id]}, 'after_build')


def _int_list(v):
    return isinstance(v, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in v)


def ota_now(req):
    dev = devices.get(req.params['id'])
    if dev is None:
        raise HttpError(404, 'device not found')
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
    assigned = [r['device_id'] for r in db.conn().execute(
        'SELECT device_id FROM device_projects WHERE project_id=? ORDER BY device_id', (pid,))]
    ids = (req.json or {}).get('device_ids')
    if ids is None:
        if not assigned:
            raise HttpError(400, 'project not assigned to any phone')
        ids = assigned
    elif not ids or not _int_list(ids):
        raise HttpError(400, 'device_ids must be a non-empty list of integers')
    elif not set(ids) <= set(assigned):
        raise HttpError(400, 'device not assigned')
    return 200, {'job_ids': [jobs.enqueue('ota', {'device_id': d, 'project_ids': [pid]}, 'manual')
                             for d in dict.fromkeys(ids)]}


def _recovery(req, mode):
    install_id = (req.json or {}).get('install_id')
    if not isinstance(install_id, int) or isinstance(install_id, bool):
        raise HttpError(400, 'install_id must be an integer')
    r = db.conn().execute('SELECT device_id, package FROM ota_installs WHERE id=? AND job_id=?',
                          (install_id, req.params['job_id'])).fetchone()
    if r is None:
        raise HttpError(404, 'install not found')
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
