"""Enabled apps (spec §3, apps part §2-§6): item rows, settings, patches, build input, rebuild fingerprint,
the update run and the global update schedule. No routes: market.py serves them."""
import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import apklocate, build, catalog, db, jobs, projects, scheduler, server, upstream

KEYSTORE = {'morphe': 'morphe.keystore', 'lspatch': 'lspatch-signing.p12', 'npatch': 'fiorenmas.ks'}
SETTING_KEYS = ('bundle_id', 'version', 'project_ids', 'variants', 'engine_options', 'display_name')
VERSION_MODES = ('auto', 'pinned', 'latest')
MODULE_ENGINE_OPTIONS = {'sigbypass': 0, 'injectdex': False}   # engine default of a module bundle
CHECK_VERSIONS = str(db.REPO_ROOT / 'src' / 'build' / 'check-versions.sh')
CHECK_TIMEOUT = 300           # one list-page request with solver retries
VERSIONS_MAX_AGE = timedelta(hours=24)   # the build refreshes the APKMirror version list at most once a day
URL_COLUMN = {'apkmirror': 'apkmirror_list_url', 'apkpure': 'apkpure_url'}   # download kind -> app column it needs

ROW_SQL = """SELECT i.*, b.origin, b.repo, b.engine, b.name AS bundle_name, b.upstream_id AS bundle_upstream_id,
  u.downloaded_tag AS bundle_tag, s.name AS store_name, s.category AS store_category, a.apkmirror_list_url, a.apkmirror_example_url,
  a.apkmirror_example_url2, a.apkpure_url, a.apkmirror_versions, a.apkmirror_checked_at, a.apkm_state
  FROM item i JOIN bundle b ON b.id = i.bundle_id JOIN app a ON a.package = i.package
  LEFT JOIN upstream u ON u.id = b.upstream_id LEFT JOIN store_app s ON s.package = i.package"""


def _j(text):
    return None if text is None else json.loads(text)


def _row(app_id):
    r = db.conn().execute(ROW_SQL + ' WHERE i.id = ?', (app_id,)).fetchone()
    if r is None:
        raise server.HttpError(404, f'app {app_id} not found')
    return r


def _label(r):
    return r['display_name'] or r['store_name'] or r['package']


def _missing_url(r):
    """Download kind of the first variant whose kind needs an app URL that is not set, else None."""
    for v in json.loads(r['variants']):
        col = URL_COLUMN.get(v['download']['kind'])
        if col is not None and not r[col]:
            return v['download']['kind']
    return None


def _status(r, st):
    out: dict[str, object] = {'state': None, 'job_id': None, 'progress': None, 'fail_code': None, 'fail_detail': None}
    active, last = st['active_job'], st['last_build']
    if active is not None:
        out.update(state='queued' if active['status'] == 'queued' else 'building', job_id=active['job_id'])
        if active['status'] == 'running' and active['step'] is not None:
            out['progress'] = {'step': active['step'], 'index': active['step_index'], 'count': active['step_count']}
    elif _missing_url(r):
        out['state'] = 'needs_download_url'
    elif last is not None and last['status'] == 'failed':
        detail = db.conn().execute('SELECT fail_detail FROM build_jobs WHERE job_id = ?', (last['job_id'],)).fetchone()
        out.update(state='failed', job_id=last['job_id'], fail_code=last['fail_code'],
                   fail_detail=detail['fail_detail'] if detail else last['reason'])
    elif st['last_success'] is None:
        out['state'] = 'not_built'
    elif build.needs_build(r['id']):
        out['state'] = 'update_available'
    else:
        out['state'] = 'up_to_date'
    return out


def _app_obj(r, st):
    ls = st['last_success']
    return {'id': r['id'], 'package': r['package'], 'name': _label(r), 'enabled': bool(r['enabled']),
            'icon_url': f"/api/apps/{r['package']}/icon", 'category': r['store_category'], 'version_mode': r['version_mode'],
            'bundle': {'bundle_id': r['bundle_id'], 'name': r['bundle_name'], 'origin': r['origin'],
                       'engine': r['engine'], 'tag': r['bundle_tag']},
            'app_version': ls['app_version'] if ls else None, 'status': _status(r, st)}


def _build_facts(item_id, abi):
    """The built APK a phone of this ABI would install (spec install-states 1): its package can differ from
    item.package (package-rename patch). None when there is no success build or its artifact has no package."""
    a = build.latest_artifact(item_id, abi)
    if a is None or not a['package']:
        return None
    return {'package': a['package'], 'version_code': a['version_code'], 'version_name': a['version_name'],
            'patch_version': a['patches_version'], 'signer_sha256': a['signer_sha256']}


def list_enabled(abi='universal'):
    rows = db.conn().execute(ROW_SQL + ' WHERE i.enabled = 1 ORDER BY i.id').fetchall()
    status = build.item_status([r['id'] for r in rows])
    return [dict(_app_obj(r, status[r['id']]), build=_build_facts(r['id'], abi)) for r in rows]


def _supported(r):
    """Union of the downloaded bundle's compatible versions for the package, newest first ([] = any or unknown)."""
    _, _, patches = catalog.patch_list(r['bundle_id'], r['package'])
    return sorted({v for p in patches or () for v in p['compatible_versions']}, key=catalog.version_key, reverse=True)


def get(app_id):
    r = _row(app_id)
    o = _app_obj(r, build.item_status([r['id']])[r['id']])
    o['settings'] = {
        'version': {'mode': r['version_mode'], 'pin': r['version_pin'], 'allow_nearest': bool(r['allow_nearest']),
                    'supported': _supported(r), 'apkmirror': _j(r['apkmirror_versions']) or []},
        'project_ids': projects.item_projects(r['id']), 'variants': json.loads(r['variants']),
        'engine_options': _j(r['engine_options']), 'display_name': r['display_name'],
        'stale_overrides': stale_overrides(r['id'])}
    return o


# ---- enable, bundle choice, settings ----

def _choose_bundle(package, bundle_id):
    """The app_bundles entry of bundle_id for package, after the catalog checks of spec §3.4 / API part §2.2."""
    try:
        bundles = catalog.app_bundles(package)
    except KeyError:
        raise server.HttpError(404, f'package {package} not found in the catalog') from None
    b = next((x for x in bundles if x['bundle_id'] == bundle_id), None)
    if b is None:
        row = db.conn().execute('SELECT host, repo FROM bundle WHERE id = ?', (bundle_id,)).fetchone()
        if row is None:
            raise server.HttpError(404, f'bundle {bundle_id} not found')
        pkgs = [x[0] for x in db.conn().execute(
            'SELECT package FROM bundle_app WHERE bundle_id = ? ORDER BY package', (bundle_id,))]
        raise server.HttpError(409, f"bundle {bundle_id} ({row['host']} {row['repo']}): does not support package "
                                    f"{package} (supports {len(pkgs)} packages: {', '.join(pkgs)})",
                               code='bundle_unsupported')
    if not b['selectable']:
        raise server.HttpError(409, f"bundle {bundle_id} ({b['host']} {b['repo']}): {b['not_selectable_reason']}",
                               code='bundle_not_selectable')
    return b


def _overrides(b, choices):
    """{name: bool} from POST /api/apps -> patch_overrides entries; names outside the bundle are 400."""
    if not isinstance(choices, dict):
        raise server.HttpError(400, f'patches expected an object of patch name to true/false, got {choices!r}')
    names = {p['name'] for p in b['patches']}
    out = {}
    for name, on in choices.items():
        if name not in names:
            raise server.HttpError(400, f"bundle {b['bundle_id']} ({b['repo']}): unknown patch {name!r}, expected one "
                                        f"of its {len(names)} patches", code='patch_unknown')
        if not isinstance(on, bool):
            raise server.HttpError(400, f'patch {name!r}: expected true or false, got {on!r}')
        out[name] = {'enabled': on}
    return out


def _switch_engine(old_engine, new_engine, variants, engine_options):
    """(variants, engine_options) after a bundle change: the engine kind decides the defaults (apps part §2.1)."""
    if old_engine is not None and catalog.is_module(old_engine) == catalog.is_module(new_engine):
        return variants, engine_options
    if catalog.is_module(new_engine):
        # ponytail: split/lib stripping is not available to module engines, so it is cleared, not refused
        return [dict(v, strip_libs=None, exclude_splits=None) for v in variants], dict(MODULE_ENGINE_OPTIONS)
    return variants, None


def enable(package, bundle_id, overrides=None):
    b = _choose_bundle(package, bundle_id)
    ov = _overrides(b, overrides) if overrides is not None else {}
    cur = db.conn().execute('SELECT i.*, x.engine FROM item i JOIN bundle x ON x.id = i.bundle_id WHERE i.package = ?',
                            (package,)).fetchone()
    if cur is not None and cur['enabled']:
        raise server.HttpError(409, f"app {cur['id']} ({package}) is already enabled", code='already_enabled')
    catalog.ensure_upstream(bundle_id)
    with db.tx() as c:
        c.execute('INSERT OR IGNORE INTO app(package) VALUES (?)', (package,))
        if cur is None:
            variants, eo = _switch_engine(None, b['engine'], catalog.default_variants(), None)
            iid = c.execute('INSERT INTO item(package, bundle_id, enabled, variants, patch_overrides, engine_options) '
                            'VALUES (?, ?, 1, ?, ?, ?)', (package, bundle_id, json.dumps(variants),
                                                          json.dumps(ov, sort_keys=True),
                                                          None if eo is None else json.dumps(eo))).lastrowid
        else:
            iid = cur['id']
            variants, eo = _switch_engine(cur['engine'], b['engine'], json.loads(cur['variants']), _j(cur['engine_options']))
            merged = {**json.loads(cur['patch_overrides']), **ov}
            c.execute('UPDATE item SET bundle_id = ?, enabled = 1, variants = ?, patch_overrides = ?, engine_options = ? '
                      'WHERE id = ?', (bundle_id, json.dumps(variants), json.dumps(merged, sort_keys=True),
                                       None if eo is None else json.dumps(eo), iid))
    jobs.enqueue('upstream', {'item_id': iid}, 'manual')
    return get(iid)


def disable(app_id):
    _row(app_id)
    with db.tx() as c:
        c.execute('UPDATE item SET enabled = 0 WHERE id = ?', (app_id,))


def _version(v):
    if not isinstance(v, dict) or set(v) - {'mode', 'pin', 'allow_nearest'}:
        raise server.HttpError(400, f'version expected an object with mode, pin, allow_nearest, got {v!r}')
    mode = v.get('mode')
    if mode not in VERSION_MODES:
        raise server.HttpError(400, f"version.mode expected one of {', '.join(VERSION_MODES)}, got {mode!r}")
    pin = server.validate_slug(v.get('pin'), 'version.pin') if mode == 'pinned' else None
    near = v.get('allow_nearest', False)
    if not isinstance(near, bool):
        raise server.HttpError(400, f'version.allow_nearest expected true or false, got {near!r}')
    return {'version_mode': mode, 'version_pin': pin, 'allow_nearest': int(near)}


def update_settings(app_id, fields):
    r = _row(app_id)
    if not isinstance(fields, dict):
        raise server.HttpError(400, f'app {app_id}: settings expected an object, got {fields!r}')
    unknown = sorted(set(fields) - set(SETTING_KEYS))
    if unknown:
        raise server.HttpError(400, f"app {app_id}: unknown setting {unknown[0]!r}, expected {', '.join(SETTING_KEYS)}")
    sets, engine = {}, r['engine']
    variants, eo = json.loads(r['variants']), _j(r['engine_options'])
    new_bundle = 'bundle_id' in fields and fields['bundle_id'] != r['bundle_id']
    if new_bundle:
        if not server.is_int(fields['bundle_id']):
            raise server.HttpError(400, f"bundle_id expected an integer, got {fields['bundle_id']!r}")
        engine = _choose_bundle(r['package'], fields['bundle_id'])['engine']
        variants, eo = _switch_engine(r['engine'], engine, variants, eo)
        sets.update(bundle_id=fields['bundle_id'], variants=json.dumps(variants),
                    engine_options=None if eo is None else json.dumps(eo))
    if 'version' in fields:
        sets.update(_version(fields['version']))
    if 'variants' in fields:
        if catalog.is_module(engine):
            catalog.check_xposed_variants(fields['variants'])
        variants = catalog.validate_variants(fields['variants'])
        sets['variants'] = json.dumps(variants)
    if 'engine_options' in fields:
        eo = catalog.validate_engine_options(engine, fields['engine_options'])
        sets['engine_options'] = None if eo is None else json.dumps(eo)
    if 'display_name' in fields:
        sets['display_name'] = catalog.display_name(fields['display_name'])
    deps = None
    if 'project_ids' in fields:
        deps = server.int_list(fields['project_ids'], 'project_ids')
        unknown = set(deps) - {x[0] for x in db.conn().execute('SELECT id FROM project')}
        if unknown:
            raise server.HttpError(400, f'project_ids: unknown project {min(unknown)}')
    if new_bundle:
        catalog.ensure_upstream(fields['bundle_id'])
    with db.tx() as c:
        if sets:   # one statement: the version_pin CHECK spans two columns; names come from the fixed keys above
            c.execute(f"UPDATE item SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), app_id))
        if deps is not None:
            c.execute('DELETE FROM item_dependency WHERE item_id = ?', (app_id,))
            c.executemany('INSERT INTO item_dependency(item_id, project_id) VALUES (?, ?)', [(app_id, d) for d in deps])
    if 'variants' in fields:
        build.drop_variants(app_id, [v['name'] for v in variants])
    if new_bundle and r['enabled']:
        jobs.enqueue('upstream', {'item_id': app_id}, 'manual')   # same first fetch + build as enable (apps part §4.6)
    return get(app_id)


# ---- patches (moved from catalog.py, logic unchanged) ----

def _reject_module(r):
    if catalog.is_module(r['engine']):
        raise server.HttpError(409, f"app {r['id']} ({r['package']}): bundle {r['bundle_id']} is an Xposed module "
                                    f"({r['engine']}) and has no patch list")


def get_patches(app_id):
    r = _row(app_id)
    _reject_module(r)
    tag, files, bundle = catalog.patch_list(r['bundle_id'], r['package'])
    ov = json.loads(r['patch_overrides'])
    if bundle is None:
        return {'tag': tag, 'files': files, 'patches': [], 'stale_overrides': [], 'compatible_versions': [],
                'unavailable': True}
    names = {p['name'] for p in bundle}
    patches = []
    for p in bundle:
        o = ov.get(p['name'], {})
        values = o.get('options', {})
        patches.append({'name': p['name'], 'description': p['description'], 'default_enabled': p['default_enabled'],
                        'enabled': o.get('enabled', p['default_enabled']),
                        'options': [dict(x, value=values.get(x['key'], x['default'])) for x in p['options']],
                        'compatible_versions': p['compatible_versions']})
    union = sorted({v for p in bundle for v in p['compatible_versions']}, key=catalog.version_key, reverse=True)
    return {'tag': tag, 'files': files, 'patches': patches, 'stale_overrides': sorted(n for n in ov if n not in names),
            'compatible_versions': union, 'unavailable': False}


def _type_ok(t, v):
    if v is None:
        return True
    t = (t or '').split('<')[0].rsplit('.', 1)[-1].lower()  # CLI prints kotlin.String, kotlin.collections.List<…>
    if t.endswith('array') or t.endswith('list') or t.startswith('list'):
        return isinstance(v, list)
    if t in ('boolean', 'bool'):
        return isinstance(v, bool)
    if t in ('int', 'integer', 'long'):
        return server.is_int(v)
    if t in ('float', 'double'):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == 'string':
        return isinstance(v, str)
    return isinstance(v, (str, int, float, bool, list))


def put_patches(app_id, body):
    r = _row(app_id)
    _reject_module(r)
    tag, _, bundle = catalog.patch_list(r['bundle_id'], r['package'])
    if bundle is None:
        raise server.HttpError(409, f"app {app_id} ({r['package']}): patch list unavailable for bundle "
                                    f"{r['bundle_id']} at tag {tag}")
    ov = (body or {}).get('overrides')
    if not isinstance(ov, dict):
        raise server.HttpError(400, f'overrides expected an object, got {ov!r}')
    by_name = {p['name']: p for p in bundle}
    # stale entries (not in this bundle) stay server-side; the body replaces the bundle entries ({} = reset)
    clean = {n: v for n, v in json.loads(r['patch_overrides']).items() if n not in by_name}
    for name, o in ov.items():
        if name not in by_name:
            raise server.HttpError(400, f'unknown patch: {name}')
        if not isinstance(o, dict) or set(o) - {'enabled', 'options'}:
            raise server.HttpError(400, f'invalid override: {name}')
        e = {}
        if 'enabled' in o:
            if not isinstance(o['enabled'], bool):
                raise server.HttpError(400, f'invalid enabled: {name}')
            e['enabled'] = o['enabled']
        if 'options' in o:
            if not isinstance(o['options'], dict):
                raise server.HttpError(400, f'invalid options: {name}')
            types = {x['key']: x['type'] for x in by_name[name]['options']}
            for k, v in o['options'].items():
                if k not in types:
                    raise server.HttpError(400, f'unknown option: {name}.{k}')
                if not _type_ok(types[k], v):
                    raise server.HttpError(400, f'invalid value: {name}.{k}')
            e['options'] = o['options']
        clean[name] = e
    with db.tx() as c:
        c.execute('UPDATE item SET patch_overrides = ? WHERE id = ?', (json.dumps(clean, sort_keys=True), app_id))
    return get_patches(app_id)


def stale_overrides(item_id):
    r = _row(item_id)
    _, _, bundle = catalog.patch_list(r['bundle_id'], r['package'])
    if bundle is None:
        return []
    names = {p['name'] for p in bundle}
    return sorted(n for n in json.loads(r['patch_overrides']) if n not in names)


# ---- buttons ----

def _require_enabled(r):
    if not r['enabled']:
        raise server.HttpError(409, f"app {r['id']} ({r['package']}) is disabled", code='app_disabled')


def check_update(app_id):
    r = _row(app_id)
    _require_enabled(r)
    return jobs.enqueue('upstream', {'item_id': r['id']}, 'manual')


def rebuild(app_id):
    r = _row(app_id)
    _require_enabled(r)
    kind = _missing_url(r)
    if kind is not None:
        what = 'an APKMirror listing URL' if kind == 'apkmirror' else 'an APKPure URL'
        raise server.HttpError(409, f"app {r['id']} ({r['package']}): build needs {what}; lookup state is "
                                    f"{r['apkm_state']}", code='needs_download_url')
    return jobs.enqueue('build', {'item_id': r['id']}, 'manual')


# ---- global schedule (one row of kind update, apps part §2.4) ----

def _schedule_row():
    r = db.conn().execute("SELECT * FROM schedules WHERE kind = 'update'").fetchone()
    if r is None:
        raise LookupError('no schedules row of kind update: boot runs scheduler.ensure_update()')
    return r


def get_schedule():
    r = _schedule_row()
    return {'id': r['id'], 'spec': json.loads(r['spec']), 'enabled': bool(r['enabled']),
            'options': json.loads(r['options']), 'last_run_at': r['last_run_at'], 'next_run_at': r['next_run_at']}


def put_schedule(fields):
    r = _schedule_row()
    if not isinstance(fields, dict) or set(fields) - {'spec', 'enabled', 'options'}:
        raise server.HttpError(400, f'schedule expected an object with spec, enabled, options, got {fields!r}')
    cur = {'name': r['name'], 'kind': 'update', 'target': {}, 'spec': json.loads(r['spec']),
           'options': json.loads(r['options']), 'enabled': bool(r['enabled'])}
    v = scheduler._validate(fields, cur)
    nxt = db.iso(scheduler.next_run(v['spec'], scheduler._now(), db.parse_iso(r['last_run_at']))) if v['enabled'] else None
    with db.tx() as c:
        c.execute('UPDATE schedules SET spec = ?, options = ?, enabled = ?, next_run_at = ? WHERE id = ?',
                  (jobs.canon(v['spec']), jobs.canon(v['options']), int(v['enabled']), nxt, r['id']))
    scheduler.wake()
    return get_schedule()


def run_schedule():
    r = _schedule_row()
    return jobs.enqueue('upstream', {'all': True}, 'manual', r['id'], {'notify': json.loads(r['options'])['notify']})


# ---- the update run (apps part §5 with spec §3.2) ----

def check_versions(package, log, cancel_event):
    """Refresh app.apkmirror_versions from the listing page (one isolated request); failure -> app.apkm_error."""
    row = db.conn().execute('SELECT apkmirror_list_url FROM app WHERE package = ?', (package,)).fetchone()
    if row is None or not row['apkmirror_list_url']:
        raise jobs.JobFailed(f'{package}: no APKMirror list URL')

    def failed(reason):
        with db.tx() as c:
            c.execute('UPDATE app SET apkm_error = ? WHERE package = ?', (reason, package))
        return jobs.JobFailed(f'{package}: {reason}')

    try:
        apklocate.throttle(cancel_event)
    except apklocate.Blocked as e:
        raise failed(f'version check blocked: {e}') from None
    m = build._Markers(None, package)
    work = Path(tempfile.mkdtemp(prefix='versions-', dir=db.DATA / 'work'))
    try:
        work.chmod(0o770)  # the isolated runner uid shares the server's gid
        try:
            rc = jobs.run_isolated(log, ['bash', CHECK_VERSIONS, row['apkmirror_list_url']], jobs.runner_path(work),
                                   env_keys=build.SOLVER_ENV, timeout=CHECK_TIMEOUT, on_line=m.feed)
        except jobs.JobFailed as e:
            if str(e) == 'cancelled':
                raise
            raise failed(f'version check {e}') from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if cancel_event.is_set():
        return
    if rc != 0 or m.versions is None:
        raise failed(f"version check failed: {m.last_err or f'runner exited {rc} without versions'}")
    with db.tx() as c:
        c.execute('UPDATE app SET apkm_error = NULL WHERE package = ?', (package,))
    log.line(f'[+] {package}: APKMirror newest {m.versions[0]} ({len(m.versions)} versions)')


def _upstreams(items, every_project):
    ups = set()
    for r in items:
        ups.add(catalog.ensure_upstream(r['bundle_id']))
        ups.add(catalog.engine_cli(r['engine']))
        ups.update(x[0] for x in db.conn().execute(
            'SELECT p.upstream_id FROM item_dependency d JOIN project p ON p.id = d.project_id WHERE d.item_id = ?',
            (r['id'],)))
    if every_project:
        ups.update(x[0] for x in db.conn().execute('SELECT upstream_id FROM project'))
    return sorted(ups)


def run_update(job, log):
    t, failed = job.target, []
    if t.get('all'):
        try:
            catalog.refresh(log)
        except (catalog.CatalogRefreshFailed, catalog.CatalogRefreshBusy) as e:
            log.line(f'[-] catalog refresh: {e}')
            failed.append(f'catalog: {e}')
        items = db.conn().execute(ROW_SQL + ' WHERE i.enabled = 1 ORDER BY i.id').fetchall()
    else:
        r = _row(t['item_id'])
        if not r['enabled']:
            raise jobs.JobFailed(f"app {r['id']} is disabled")
        items = [r]
    for uid in _upstreams(items, bool(t.get('all'))):
        if job.cancel_event.is_set():
            return
        try:
            upstream.pull(uid, log, job_id=job.id)
        except (upstream.TokenRejected, upstream.RateLimited) as e:
            log.line(f'[-] upstream {uid}: {e}; GitHub step stopped')
            failed.append(f'upstream {uid}: {e}')
            break
        except (jobs.JobFailed, OSError) as e:
            log.line(f'[-] upstream {uid}: {e}')
            failed.append(f'upstream {uid}: {e}')
    buildable = []
    for r in items:
        if job.cancel_event.is_set():
            return
        if not r['apkmirror_list_url'] and not r['apkpure_url']:
            apklocate.locate(r['package'], log, job.cancel_event)
            r = _row(r['id'])
        if _missing_url(r):
            log.line(f"[!] app {r['id']} ({r['package']}): no download URL, lookup state {r['apkm_state']}; not built")
            continue
        if r['version_mode'] == 'latest' and r['apkmirror_list_url']:
            try:
                check_versions(r['package'], log, job.cancel_event)
            except jobs.JobFailed as e:
                if job.cancel_event.is_set():
                    return
                log.line(f'[-] {e.reason}')
                failed.append(f'version check {e.reason}')
        buildable.append(r['id'])
    if job.cancel_event.is_set():
        return
    notify = (job.options or {}).get('notify', True)
    queued = [i for i in buildable if build.needs_build(i)]
    for i in queued:
        jobs.enqueue('build', {'item_id': i}, job.trigger, job.schedule_id, {'notify': notify})
    log.line(f'[+] build enqueued for {len(queued)} app(s)')
    if failed:
        raise jobs.JobFailed(f"{len(failed)} steps failed: {'; '.join(failed)}")


# ---- build input and fingerprint (apps part §4) ----

def _want(r):
    """(value for job.json, version for the fingerprint) of one row (apps part §4.2)."""
    if r['version_mode'] == 'pinned':
        return r['version_pin'], r['version_pin']
    if r['version_mode'] == 'auto':
        supported = _supported(r)
        if supported:
            return supported[0], supported[0]
    newest = (_j(r['apkmirror_versions']) or [None])[0]
    return None, newest   # latest, or auto with no compatible list: the runner takes the newest it sees


def _overrides_in_use(r, patches):
    names = {p['name'] for p in patches or ()}
    return {k: v for k, v in json.loads(r['patch_overrides']).items() if k in names}


def _versions_due(checked_at):
    return checked_at is None or db.parse_iso(checked_at) < datetime.now(timezone.utc) - VERSIONS_MAX_AGE


def job_input(item_id):
    r = db.conn().execute(ROW_SQL + ' WHERE i.id = ?', (item_id,)).fetchone()
    if r is None:
        raise catalog.UpstreamMissing(f'app {item_id} not found')
    tag, names, patches = catalog.patch_list(r['bundle_id'], r['package'])
    if not tag:
        raise catalog.UpstreamMissing(f"bundle {r['bundle_id']} ({r['repo']}) not downloaded")
    cli_id = catalog.engine_cli(r['engine'])
    cli_tag = db.conn().execute('SELECT downloaded_tag FROM upstream WHERE id = ?', (cli_id,)).fetchone()[0]
    if not cli_tag:
        raise catalog.UpstreamMissing(f"{r['engine']} CLI upstream {cli_id} not downloaded")
    if r['engine'] == 'morphe' and patches is None:
        raise catalog.UpstreamMissing(f"patch list missing for {r['package']} at {tag}")
    pdir, cdir = upstream.tag_dir(r['bundle_upstream_id'], tag), upstream.tag_dir(cli_id, cli_tag)
    files = [str(pdir / n) for n in names]
    jars = sorted(str(p) for p in cdir.glob('*.jar')) if cdir.is_dir() else []
    if not files or not jars:
        raise catalog.UpstreamMissing('downloaded files missing on disk')
    options = [{'patches': _overrides_in_use(r, patches)} for _ in files] if r['engine'] == 'morphe' else None
    return {
        'item_id': r['id'], 'package': r['package'], 'engine': r['engine'], 'engine_options': _j(r['engine_options']),
        'upstream_tag': tag, 'cli_tag': cli_tag, 'patches_files': files, 'cli_jar': jars[0],
        'options_file_json': options, 'version': {'value': _want(r)[0], 'near': bool(r['allow_nearest'])},
        'apkmirror': {'list_url': r['apkmirror_list_url'], 'example_url': r['apkmirror_example_url'],
                      'example_url2': r['apkmirror_example_url2'],
                      'versions_due': _versions_due(r['apkmirror_checked_at'])},
        'apkpure_url': r['apkpure_url'], 'keystore': KEYSTORE[r['engine']],
        'recipe': {'variants': json.loads(r['variants'])},
    }


def inputs_sha(item_id):
    r = _row(item_id)
    tag, _, patches = catalog.patch_list(r['bundle_id'], r['package'])
    cli_tag = db.conn().execute('SELECT downloaded_tag FROM upstream WHERE id = ?',
                                (catalog.engine_cli(r['engine']),)).fetchone()[0]
    base = {'bundle_id': r['bundle_id'], 'patches_tag': tag, 'cli_tag': cli_tag, 'version': _want(r)[1],
            'overrides': _overrides_in_use(r, patches), 'engine_options': _j(r['engine_options'])}
    h = lambda o: hashlib.sha256(jobs.canon(o).encode()).hexdigest()  # noqa: E731
    return json.dumps({'base': h(base), 'variants': {v['name']: h(v) for v in json.loads(r['variants'])}}, sort_keys=True)


def inputs_cover(stored, current):
    """True when the build fingerprinted stored made every variant current asks for: dropping a variant needs no rebuild."""
    if not stored or not stored.startswith('{'):
        return False   # NULL or a fingerprint from before per-variant hashes: rebuilt once
    s, c = json.loads(stored), json.loads(current)
    return s['base'] == c['base'] and c['variants'].items() <= s['variants'].items()
