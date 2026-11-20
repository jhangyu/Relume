"""App Manage ADB job kind `appmanage` (plan 2026-11-20 §1.9). Targets: {'action_id': n} runs one queued action on an ADB
device, {'device_id': n, 'refresh': True} re-reads its app inventory. Log lines carry hosts and package names, never a
Play URL query."""
import re
import urllib.parse
import urllib.request

from . import appmanage_play, appmanage_store as store, db, devices, events, jobs, ota

ADB_TIMEOUT = 120        # seconds for one short adb call (install-create, commit, uninstall, am start)
WRITE_TIMEOUT = 900      # one APK streamed through install-write
DOWNLOAD_TIMEOUT = 60    # socket timeout of the Play download
PLAY_PAGE_URL = "'market://details?id={}'"
_SESSION = re.compile(r'\[(\d+)\]')
REINVENTORY = ('install', 'upgrade', 'remove')


def _reinventory(job, dev, log):
    apps = devices.app_inventory(dev, job.cancel_event)
    store.ingest_full(dev['id'], apps)
    events.changed('appmanage')
    log.line(f'inventory of {dev["name"]}: {len(apps)} user apps')


def _refresh(job, log, device_id):
    dev = devices.get(device_id)
    if dev is None:
        raise jobs.JobFailed('device removed')
    if devices.is_app(dev):
        raise jobs.JobFailed(devices.APP_ONLY)
    state = devices.reconnect(dev, cancel_event=job.cancel_event, log=log)
    if state != 'online':
        raise jobs.JobFailed('DEVICE_OFFLINE' if state == 'offline' else state.upper())
    _reinventory(job, dev, log)
    for a in store.actions(device_id):   # actions parked by an offline run re-run at the refresh
        if a['state'] == 'queued':
            jobs.enqueue('appmanage', {'action_id': a['id']}, 'manual')


def _stream_apk(job, serial, sid, apk, log):
    """Download one APK and pipe it into the session. -> (error_code, text) or None."""
    host = urllib.parse.urlsplit(apk['url']).hostname
    log.line(f"downloading {apk['name']} from {host}")
    try:
        with urllib.request.urlopen(apk['url'], timeout=DOWNLOAD_TIMEOUT) as resp:
            size = resp.headers.get('Content-Length')
            if size is None or not size.isdigit():
                return 'PLAY_DOWNLOAD_FAILED', f"expected a Content-Length for {apk['name']} from {host}, got {size!r}"
            rc, out = devices.adb_pipe(['shell', 'pm', 'install-write', '-S', size, sid, apk['name'], '-'], resp,
                                       serial=serial, timeout=WRITE_TIMEOUT, cancel_event=job.cancel_event, log=log)
    except OSError as e:   # URLError, HTTPError and socket errors; HTTPError text has no URL
        return 'PLAY_DOWNLOAD_FAILED', f"expected a download of {apk['name']} from {host}, got {e}"
    if rc != 0 or 'Success' not in out:
        return devices.install_error(out), f"expected install-write of {apk['name']} to succeed, got rc {rc}: {out.strip()}"
    return None


def _install_session(job, dev, sid, apks, log):
    serial = devices.address(dev)
    for apk in apks:
        err = _stream_apk(job, serial, sid, apk, log)
        if err:
            return err
    rc, out = devices.adb(['shell', 'pm', 'install-commit', sid], serial=serial, timeout=ADB_TIMEOUT,
                          cancel_event=job.cancel_event, log=log)
    code = devices.install_error(out)
    if code or rc != 0:
        return code or 'ADB_ERROR', f'expected install-commit to succeed, got rc {rc}: {out.strip()}'
    return None


def _play_direct(job, dev, package, log):
    try:
        resolved = appmanage_play.resolve(package, dev, log)
    except appmanage_play.PlayLookupError as e:
        return e.code, str(e)
    serial = devices.address(dev)
    rc, out = devices.adb(['shell', 'pm', 'install-create', '-r'], serial=serial, timeout=ADB_TIMEOUT,
                          cancel_event=job.cancel_event, log=log)
    m = _SESSION.search(out)
    if rc != 0 or m is None:
        return 'ADB_ERROR', f'expected install-create to print a session id, got rc {rc}: {out.strip()}'
    sid, ok = m.group(1), False
    try:
        err = _install_session(job, dev, sid, resolved['apks'], log)
        ok = err is None
        return err
    finally:
        if not ok:   # also on cancel and adb timeout; no cancel_event here, the abandon must run
            devices.adb(['shell', 'pm', 'install-abandon', sid], serial=serial, timeout=ADB_TIMEOUT, log=log)


def _remove(job, dev, package, log):
    rc, out = devices.adb(['uninstall', package], serial=devices.address(dev), timeout=ADB_TIMEOUT,
                          cancel_event=job.cancel_event, log=log)
    if rc != 0 or 'Success' not in out:
        return 'UNINSTALL_FAILED', f'expected uninstall to print Success, got rc {rc}: {out.strip()}'
    return None


def _play_page(job, dev, package, log):
    rc, out = devices.adb(['shell', 'am', 'start', '-a', 'android.intent.action.VIEW', '-d',
                           PLAY_PAGE_URL.format(package)], serial=devices.address(dev), timeout=ADB_TIMEOUT,
                          cancel_event=job.cancel_event, log=log)
    if rc != 0 or 'Error' in out:
        return 'ACTIVITY_START_FAILED', f'expected the Play page to open, got rc {rc}: {out.strip()}'
    return None


def _execute(job, dev, act, log):
    """-> (error_code, text) or None."""
    if act['action'] == 'remove':
        return _remove(job, dev, act['package'], log)
    if act['source'] == 'build':
        code, text = ota.install_ref(job, dev, act['ref_kind'], act['ref_id'], log)
        return (code, text) if code else None
    if act['source'] == 'play_direct':
        return _play_direct(job, dev, act['package'], log)
    return _play_page(job, dev, act['package'], log)


def _park(job, act):
    """Offline device: the action goes back to queued and the next refresh re-runs it."""
    with db.tx() as c:
        c.execute("UPDATE device_actions SET state='queued', job_id=NULL, updated_at=? WHERE id=?", (db.utcnow(), act['id']))
        c.execute('UPDATE jobs SET reason=? WHERE id=?', ('DEVICE_OFFLINE', job.id))


def _finish(job, dev, act, err):
    if err is None:
        store.finish(dev['id'], act['id'], 'done')
    elif job.cancel_event.is_set():
        store.finish(dev['id'], act['id'], 'cancelled', 'CANCELLED', 'cancelled on the web')
    else:
        code, text = err
        if code not in store.ERROR_CODES:
            code, text = 'ADB_ERROR', f'{code}: {text}'
        store.finish(dev['id'], act['id'], 'failed', code, f"{text} on {dev['name']} for {act['package']}")
    events.changed('appmanage')


def _action(job, log, action_id):
    act = store.claim_for_job(action_id, job.id)
    if act is None:
        log.line(f'action {action_id} is not queued any more (cancelled or replaced): nothing to do')
        return
    dev = devices.get(act['device_id'])
    state = devices.reconnect(dev, cancel_event=job.cancel_event, log=log)
    if state != 'online':
        log.line(f"{dev['name']} is {state}: action {action_id} stays queued")
        _park(job, act)
        events.changed('appmanage')
        return
    log.line(f"{act['action']} {act['package']} on {dev['name']} ({act['source'] or 'adb uninstall'})")
    try:
        err = _execute(job, dev, act, log)
    except devices.AdbTimeout as e:
        err = ('TIMEOUT', f'expected adb to answer in time, got {e}')
    except Exception as e:   # the action must not stay running; the job still fails with the traceback
        _finish(job, dev, act, ('ADB_ERROR', f'{type(e).__name__}: {e}'))
        raise
    _finish(job, dev, act, err)
    if act['action'] in REINVENTORY and not job.cancel_event.is_set():
        _reinventory(job, dev, log)


def handle(job, log):
    if 'action_id' in job.target:
        _action(job, log, job.target['action_id'])
    else:
        _refresh(job, log, job.target['device_id'])


def detail(job_id):
    r = db.conn().execute('SELECT * FROM device_actions WHERE job_id=?', (job_id,)).fetchone()
    return {'action': dict(r) if r else None}
