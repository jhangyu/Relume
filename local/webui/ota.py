"""OTA part: planner, read model, OTA job handler, recovery routes, after-build OTA (spec device 3-4)."""
import importlib
import json
import os

from . import db, devices, events, jobs
from .server import HttpError


def _item_dependencies(item_id):
    return importlib.import_module('webui.catalog').item_dependencies(item_id)


def _latest_artifact(item_id, abi):
    return importlib.import_module('webui.build').latest_artifact(item_id, abi)


def first_abi(dev):
    abis = [a.strip() for a in (dev.get('abi_list') or '').split(',') if a.strip()]
    return abis[0] if abis else 'universal'


def assigned_items(device_id, item_ids=None):
    """Assigned items in catalog order (groups by position, Ungrouped last); disabled items included."""
    rows = db.conn().execute(
        'SELECT item.id AS item_id, item.package FROM device_assignments da JOIN item ON item.id = da.item_id '
        'LEFT JOIN grp ON grp.id = item.group_id WHERE da.device_id = ? '
        'ORDER BY grp.position IS NULL, grp.position, item.position, item.id', (device_id,)).fetchall()
    items = [dict(r) for r in rows]
    if item_ids is not None:
        items = [i for i in items if i['item_id'] in set(item_ids)]
    return items


def last_installs(device_id):
    items, deps = {}, {}
    rows = db.conn().execute(
        "SELECT oi.kind, oi.ref_id, oi.build_job_id, oi.upstream_tag, oi.version_name, j.finished_at "
        "FROM ota_installs oi JOIN jobs j ON j.id = oi.job_id "
        "WHERE oi.device_id = ? AND oi.result IN ('installed','up_to_date') ORDER BY oi.id", (device_id,))
    for r in rows:  # oldest first: newer rows overwrite
        if r['kind'] == 'item':
            items[r['ref_id']] = {'build_job_id': r['build_job_id'], 'version_name': r['version_name'],
                                  'at': r['finished_at']}
        else:
            deps[r['ref_id']] = {'upstream_tag': r['upstream_tag'], 'at': r['finished_at']}
    return items, deps


def plan(items, artifacts, deps_by_item, item_installs, dep_installs, present):
    """Pure D7/D8 planner. present=None means 'unknown': every package counts as present."""
    def has(pkg):
        return present is None or pkg in present
    steps, planned = [], set()
    for it in items:
        iid, pkg, a = it['item_id'], it['package'], artifacts.get(it['item_id'])
        deps = deps_by_item.get(iid, [])
        step = {'kind': 'item', 'ref_id': iid, 'package': pkg, 'artifact': a, 'needs': [d['upstream_id'] for d in deps]}
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
                if d['upstream_id'] in planned:
                    continue
                prev = dep_installs.get(d['upstream_id'])
                if not has(d['package']):
                    why = 'missing on phone'
                elif prev is None or prev['upstream_tag'] != d['tag']:
                    why = 'new release'
                else:
                    continue
                planned.add(d['upstream_id'])
                steps.append({'kind': 'dependency', 'ref_id': d['upstream_id'], 'dependency_app_id': d['dependency_app_id'],
                              'package': d['package'], 'path': d['path'], 'tag': d['tag'], 'action': 'install',
                              'reason': why})
        steps.append(dict(step, action=action, reason=reason))
    return steps


def _plan_for(dev, item_ids, present):
    abi = first_abi(dev)
    items = assigned_items(dev['id'], item_ids)
    item_inst, dep_inst = last_installs(dev['id'])
    arts = {i['item_id']: _latest_artifact(i['item_id'], abi) for i in items}
    deps = {i['item_id']: _item_dependencies(i['item_id']) for i in items}
    return items, arts, deps, item_inst, dep_inst, plan(items, arts, deps, item_inst, dep_inst, present)


def _device_view(dev, states):
    items, arts, deps, item_inst, dep_inst, steps = _plan_for(dev, None, None)
    pending = {s['ref_id'] for s in steps if s['kind'] == 'item' and s['action'] == 'install'}
    assignments = []
    for i in items:
        a, inst = arts[i['item_id']], item_inst.get(i['item_id'])
        assignments.append({
            'item_id': i['item_id'],
            'installed': inst and {'version_name': inst['version_name'], 'build_job_id': inst['build_job_id'],
                                   'at': inst['at']},
            'available': a and {'version_name': a['version_name'], 'build_job_id': a['job_id']},
            'pending': i['item_id'] in pending})
    dmap = {}
    for iid, ds in deps.items():
        for d in ds:
            e = dmap.setdefault(d['upstream_id'], {
                'dependency_app_id': d['dependency_app_id'], 'display_name': d['display_name'], 'package': d['package'],
                'installed_tag': (dep_inst.get(d['upstream_id']) or {}).get('upstream_tag'),
                'available_tag': d['tag'], 'required_by': []})
            e['required_by'].append(iid)
    return dict(devices.device_json(dev, states), assignments=assignments, dependencies=list(dmap.values()))


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


def plan_route(req):
    dev = devices.get(req.params['id'])
    if dev is None:
        raise HttpError(404, 'device not found')
    online = devices.online_states().get(devices.address(dev)) == 'device'
    steps = _plan_for(dev, None, _present(dev) if online else None)[-1]
    return 200, {'online': online, 'steps': steps}


ROUTES = [
    ('GET', '/api/devices', list_devices),
    ('GET', '/api/devices/{id}/plan', plan_route),
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
    with db.tx() as c:
        c.execute('INSERT INTO ota_installs(job_id,device_id,kind,ref_id,build_job_id,upstream_tag,package,version_name,'
                  'version_code,result,error_code,error_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (job.id, device_id, step['kind'], step['ref_id'], a and a['job_id'],
                   step.get('tag') if step['kind'] == 'dependency' else None, step['package'],
                   a and a['version_name'], a and a['version_code'], result, error_code,
                   error_text[-2048:] if error_text else None))
    return result in ('installed', 'up_to_date')


def _install(job, dev, path, package, log):
    """-> (error_code or None, output). Mode force_reinstall uninstalls first (D6, manual only)."""
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


def _pull_dependency(job, dev, step, log):
    """User decision: a dependency that was never downloaded is pulled by its upstream job, then installed."""
    log.line(f"dependency {step['package']} not downloaded: pulling upstream {step['ref_id']}")
    jid = jobs.enqueue('upstream', {'upstream_id': step['ref_id']}, job.trigger,
                       options={'rebuild': False, 'notify': job.options.get('notify', True)})
    while True:
        row = jobs.get(jid)
        if row is None or row['status'] in _DONE:
            break
        if job.cancel_event.wait(1):
            return None
    for i in assigned_items(dev['id']):
        for d in _item_dependencies(i['item_id']):
            if d['upstream_id'] == step['ref_id'] and d['path']:
                return d
    return None


def _run_dependency(job, dev, step, log, failed):
    if not step['path']:
        d = _pull_dependency(job, dev, step, log)
        if d is None:
            failed.add(step['ref_id'])
            return _record(job, dev['id'], step, 'failed', 'DEPENDENCY_FAILED', 'dependency not downloaded')
        step = dict(step, path=d['path'], tag=d['tag'])
    code, out = _install(job, dev, step['path'], step['package'], log)
    if code:
        failed.add(step['ref_id'])
        return _record(job, dev['id'], step, 'failed', code, out)
    return _record(job, dev['id'], step, 'installed')


def _run_item(job, dev, step, abi, log, failed):
    if failed.intersection(step['needs']):
        return _record(job, dev['id'], step, 'failed', 'DEPENDENCY_FAILED', 'dependency install failed', step['artifact'])
    if step['action'] == 'error':
        return _record(job, dev['id'], step, 'failed', 'NO_MATCHING_ABI', 'NO_MATCHING_ARTIFACT')
    if step['action'] == 'up_to_date':
        return _record(job, dev['id'], step, 'up_to_date', a=step['artifact'])
    # ponytail: re-resolve right before install, once more if the file is gone; no fd pinning. A file deleted
    # by retention between this check and adb's open fails ADB_ERROR and the next OTA retries.
    a = None
    for _ in range(2):
        a = _latest_artifact(step['ref_id'], abi)
        if a and os.path.exists(a['path']):
            break
        a = None
    if a is None:
        return _record(job, dev['id'], step, 'failed', 'NO_MATCHING_ABI', 'artifact file missing')
    code, out = _install(job, dev, a['path'], step['package'], log)
    if code:
        return _record(job, dev['id'], step, 'failed', code, out, a)
    return _record(job, dev['id'], step, 'installed', a=a)


def _recovery_steps(dev, package, abi):
    items = assigned_items(dev['id'])
    for i in items:
        if i['package'] == package:
            a = _latest_artifact(i['item_id'], abi)
            return [{'kind': 'item', 'ref_id': i['item_id'], 'package': package, 'artifact': a, 'needs': [],
                     'action': 'install' if a else 'error', 'reason': 'recovery' if a else 'NO_MATCHING_ARTIFACT'}]
    for i in items:
        for d in _item_dependencies(i['item_id']):
            if d['package'] == package:
                return [{'kind': 'dependency', 'ref_id': d['upstream_id'], 'dependency_app_id': d['dependency_app_id'],
                         'package': package, 'path': d['path'], 'tag': d['tag'], 'action': 'install',
                         'reason': 'recovery'}]
    return None


def handle(job, log):
    mode = job.options.get('mode', 'normal')
    if mode != 'normal' and job.trigger != 'manual':
        raise jobs.JobFailed('recovery requires manual trigger')
    dev = devices.get(job.target.get('device_id'))
    if dev is None:
        raise jobs.JobFailed('device removed')
    item_ids = job.target.get('item_ids')
    with db.tx() as c:
        c.execute('INSERT OR REPLACE INTO ota_jobs(job_id,device_id,item_ids,mode,outcome) VALUES (?,?,?,?,NULL)',
                  (job.id, dev['id'], None if item_ids is None else json.dumps(item_ids), mode))
    abi = first_abi(dev)
    recovery = None
    if mode != 'normal':
        recovery = _recovery_steps(dev, job.options.get('package'), abi)
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
    steps = recovery or _plan_for(dev, item_ids, _present(dev, job.cancel_event))[-1]
    ok, failed = [], set()
    for step in steps:
        if job.cancel_event.is_set():
            return
        log.line(f"{step['kind']} {step['package']}: {step['action']} ({step['reason']})")
        if step['kind'] == 'dependency':
            ok.append(_run_dependency(job, dev, step, log, failed))
        else:
            ok.append(_run_item(job, dev, step, abi, log, failed))
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
            'mode': r['mode'], 'outcome': r['outcome'],
            'installs': [dict(x) for x in c.execute('SELECT * FROM ota_installs WHERE job_id=? ORDER BY id', (job_id,))]}


def on_build_succeeded(payload):
    item_id = payload.get('item_id')
    rows = db.conn().execute('SELECT d.id FROM devices d JOIN device_assignments da ON da.device_id = d.id '
                             'WHERE d.ota_after_build = 1 AND da.item_id = ? ORDER BY d.id', (item_id,)).fetchall()
    for r in rows:
        jobs.enqueue('ota', {'device_id': r['id'], 'item_ids': [item_id]}, 'after_build')


def ota_now(req):
    dev = devices.get(req.params['id'])
    if dev is None:
        raise HttpError(404, 'device not found')
    ids = (req.json or {}).get('item_ids')
    target = {'device_id': dev['id']}
    if ids is not None:
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise HttpError(400, 'item_ids must be a non-empty list of integers')
        if not set(ids) <= {i['item_id'] for i in assigned_items(dev['id'])}:
            raise HttpError(400, 'item not assigned to this device')
        target['item_ids'] = ids
    return 200, {'job_id': jobs.enqueue('ota', target, 'manual')}


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
    ('POST', '/api/ota/{job_id}/force-reinstall', force_reinstall),
    ('POST', '/api/ota/{job_id}/downgrade', downgrade),
]
