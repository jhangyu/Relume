"""App Manage routes (plan 2026-11-20-app-manage §1.3 web, §1.4 companion). The web routes are session-only
(auth.SESSION_ONLY); the companion routes under /api/me/ reach only the calling device's own actions."""
import base64
import binascii

from . import appmanage_matrix, appmanage_play, appmanage_store as store, db, devices, jobs
from .server import FileResponse, HttpError

ICON_CACHE_SECONDS = 86400


def _devices(primary_id):
    """§1.3 device list: legacy rows excluded; primary first, then name (case-folded), then id."""
    rows = [dict(r) for r in db.conn().execute(f'SELECT * FROM devices WHERE NOT {devices.LEGACY_SQL}')]
    states = devices.online_states() if any(not devices.is_app(r) for r in rows) else {}
    reports = store.reports()
    out = [{'id': r['id'], 'name': r['name'], 'kind': r['kind'],
            'online': None if devices.is_app(r) else states.get(devices.address(r)) == 'device',
            'reported_at': reports.get(r['id'])} for r in rows]
    return sorted(out, key=lambda d: (d['id'] != primary_id, d['name'].casefold(), d['id']))


def matrix(req):
    primary = store.get_primary()
    return 200, appmanage_matrix.build(_devices(primary), store.inventory(), store.actions(), store.tracked_refs(),
                                       primary)


def put_primary(req):
    if 'device_id' not in req.json:
        raise HttpError(400, f'device_id must be an integer or null, got no device_id in {sorted(req.json)}')
    return 200, {'primary_id': store.set_primary(req.json['device_id'])}


def create_actions(req):
    j = req.json
    return 200, {'actions': store.create_actions(j.get('package'), j.get('action'), j.get('device_ids'),
                                                 j.get('source'))}


def delete_action(req):
    return 200, store.cancel(req.params['id'])


def refresh(req):
    """Inventory refresh job of every ADB device; the refresh job itself re-enqueues the device's queued actions."""
    return 200, {'job_ids': [jobs.enqueue('appmanage', {'device_id': did, 'refresh': True}, 'manual')
                             for (did,) in db.conn().execute("SELECT id FROM devices WHERE kind='adb' ORDER BY id")]}


def icon(req):
    p = store.icon_path(req.params['sha'])
    if p is None:
        raise HttpError(404, f"icon {req.params['sha']} not found")
    return FileResponse(p, cache_seconds=ICON_CACHE_SECONDS)


def me_icon(req):
    sha, b64 = req.json.get('sha256'), req.json.get('png_base64')
    if not isinstance(b64, str):
        raise HttpError(400, f'png_base64 must be a base64 string, got {type(b64).__name__} for icon {sha!r}')
    try:
        png = base64.b64decode(b64, validate=True)
    except binascii.Error as e:
        raise HttpError(400, f'png_base64 must be valid base64, got an invalid string ({e}) for icon {sha!r}')
    store.store_icon(sha, png)
    return 200, {'stored': True}


def me_start(req):
    """delivered -> running. play_direct: the Play delivery URLs are resolved now and only sent back, never stored
    or logged. A failed lookup fails the action, so it never stays running."""
    did, aid = req.device_id, req.params['id']
    row = store.start(did, aid)
    if row['source'] != 'play_direct':
        return 200, {'id': aid}
    try:
        got = appmanage_play.resolve(row['package'], devices.get(did))
    except appmanage_play.PlayLookupError as e:
        store.finish(did, aid, 'failed', e.code, str(e))
        raise HttpError(502, str(e), code=e.code)
    except Exception as e:   # recorded on the action, then re-raised: a lookup crash must not leave it running
        store.finish(did, aid, 'failed', 'PLAY_LOOKUP_FAILED',
                     f"Play lookup of {row['package']} for device {did} crashed: {type(e).__name__}: {e}")
        raise
    return 200, {'id': aid, 'version_code': got['version_code'], 'apks': got['apks']}


def me_result(req):
    j = req.json
    store.finish(req.device_id, req.params['id'], j.get('result'), j.get('error_code'), j.get('error_text'))
    return 200, {'ok': True}


def register():
    from . import appmanage_job   # lazy (plan §2 R2-A): loading the routes never imports the ADB job module
    jobs.register('appmanage', appmanage_job.handle, appmanage_job.detail)


ROUTES = [
    ('GET', '/api/appmanage', matrix),
    ('PUT', '/api/appmanage/primary', put_primary),
    ('POST', '/api/appmanage/actions', create_actions),
    ('DELETE', '/api/appmanage/actions/{id}', delete_action),
    ('POST', '/api/appmanage/refresh', refresh),
    ('GET', '/api/appmanage/icons/{sha}', icon),
    ('POST', '/api/me/icons', me_icon),
    ('POST', '/api/me/actions/{id}/start', me_start),
    ('POST', '/api/me/actions/{id}/result', me_result),
]
