"""Notifier (spec device 4.4, 6): subscribers only queue; one thread sends to the one configured channel."""
import json
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import db, events, jobs, projects, settings
from .server import HttpError

EVENTS = ('upstream.new_version', 'build.succeeded', 'build.failed', 'ota.done', 'ota.failed', 'device.offline',
          'project.signer_changed')
DEFAULT_EVENTS = tuple(e for e in EVENTS if e != 'device.offline')
TELEGRAM_API = 'https://api.telegram.org'
CONFIG_KEYS = {'telegram': {'bot_token': True, 'chat_id': True}, 'discord': {'webhook_url': True},
               'ntfy': {'server_url': True, 'topic': True, 'token': False}}
SECRETS = ('bot_token', 'webhook_url', 'token')
URL_KEYS = ('webhook_url', 'server_url')
LIMIT = 2000
_q = queue.Queue()


def _pause(seconds):
    threading.Event().wait(seconds)


def enqueue(type_, payload):
    _q.put((type_, payload))


def register():
    for name in EVENTS:
        events.subscribe(name, lambda payload, n=name: enqueue(n, payload))


def start():
    threading.Thread(target=worker, name='notifier', daemon=True).start()


def worker():
    while True:
        t, p = _q.get()
        try:
            deliver(t, p)
        except Exception as e:  # never let one event kill the notifier thread
            print(f'notify: {t}: {type(e).__name__}: {e}', flush=True)


def deliver(t, p):
    cfg = settings.get('notify')
    if not cfg or not cfg.get('enabled') or t not in cfg.get('events', DEFAULT_EVENTS):
        return None
    if p.get('job_id') is not None:
        row = jobs.get(p['job_id'])
        if row and row['options'].get('notify') is False:
            return None
    ok, status, detail = send(cfg, format_message(t, p))
    if not ok:
        print(f'notify: {t} failed: {status} {detail[:200]}', flush=True)
    return ok


def _one(sql, args, default):
    r = db.conn().execute(sql, args).fetchone()
    return r[0] if r and r[0] is not None else default


def _label(item_id):
    return _one("SELECT COALESCE(item.display_name, app.display_name || ' · ' || source.name) FROM item "
                'JOIN app ON app.package = item.package JOIN source ON source.id = item.source_id WHERE item.id = ?',
                (item_id,), f'item {item_id}')


def _project_label(col, value):
    """Label of the project whose col ('p.id' or 'p.upstream_id', fixed by the callers) equals value; None if none."""
    r = db.conn().execute(f'SELECT p.display_name, u.repo FROM project p JOIN upstream u ON u.id = p.upstream_id '
                          f'WHERE {col} = ?', (value,)).fetchone()
    return projects.label(r) if r else None


def _device(device_id):
    return _one('SELECT name FROM devices WHERE id = ?', (device_id,), f'device {device_id}')


def format_message(t, p):
    if t == 'build.succeeded':
        text = f"Build succeeded: {_label(p['item_id'])} {p.get('app_version')} (patches {p.get('patches_version')})"
        path = f"/builds.html?job={p['job_id']}"
    elif t == 'build.failed':
        text = f"Build failed: {_label(p['item_id'])}: {p.get('fail_code')}: {p.get('fail_detail')}"
        path = f"/builds.html?job={p['job_id']}"
    elif t == 'project.signer_changed':
        text = (f"Signer changed: {_project_label('p.id', p['project_id'])} {p.get('tag')}. "
                'Installs are blocked until you trust the new signer.')
        path = '/projects.html'
    elif t == 'upstream.new_version' and (label := _project_label('p.upstream_id', p['upstream_id'])):
        text, path = f"New version: {label} {p.get('tag')}", '/projects.html'
    elif t == 'upstream.new_version':
        repo = _one('SELECT repo FROM upstream WHERE id = ?', (p['upstream_id'],), f"upstream {p['upstream_id']}")
        text, path = f"New upstream version: {repo} {p.get('tag')} ({len(p.get('item_ids') or [])} items)", '/'
    elif t == 'ota.done':
        text, path = f"OTA {p.get('outcome')}: {_device(p['device_id'])}", '/devices.html'
    elif t == 'ota.failed':
        text, path = f"OTA failed: {_device(p['device_id'])}: {p.get('reason')}", '/devices.html'
    else:
        text, path = f"Device offline, scheduled OTA skipped: {_device(p['device_id'])}", '/devices.html'
    base = (settings.get('base_url') or '').rstrip('/')
    link = f'\n{base}{path}' if base else ''
    return text[:LIMIT - len(link)] + link


def _request(cfg, text):
    c, t = cfg['config'], cfg['type']
    if t == 'telegram':
        return urllib.request.Request(f"{TELEGRAM_API}/bot{c['bot_token']}/sendMessage", method='POST',
                                      data=json.dumps({'chat_id': c['chat_id'], 'text': text}).encode(),
                                      headers={'Content-Type': 'application/json'})
    if t == 'discord':
        return urllib.request.Request(c['webhook_url'], method='POST', data=json.dumps({'content': text}).encode(),
                                      headers={'Content-Type': 'application/json'})
    headers = {'Title': 'Relume'}
    if c.get('token'):
        headers['Authorization'] = f"Bearer {c['token']}"
    body = text.encode()[:4096].decode('utf-8', 'ignore').encode()
    return urllib.request.Request(f"{c['server_url'].rstrip('/')}/{urllib.parse.quote(c['topic'])}", method='POST',
                                  data=body, headers=headers)


def _retry_after(headers, body):
    try:
        j = json.loads(body)
        v = (j.get('parameters') or {}).get('retry_after', j.get('retry_after'))
        if v is not None:
            return float(v)
    except (ValueError, AttributeError):
        pass
    try:
        return float(headers.get('Retry-After'))
    except (TypeError, ValueError):
        return None


def send(cfg, text):
    """-> (ok, http status or None, response body or error text). 3 attempts, timeout 10 s each."""
    status, detail = None, ''
    for attempt in range(3):
        wait = None
        try:
            with urllib.request.urlopen(_request(cfg, text), timeout=10) as r:
                return True, r.status, r.read(2000).decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            status, detail = e.code, e.read(2000).decode('utf-8', 'replace')
            if e.code == 429:
                wait = _retry_after(e.headers, detail)
        except OSError as e:
            status, detail = None, str(e)
        if attempt < 2:
            _pause(min(wait, 60.0) if wait is not None else float(attempt + 1))
    return False, status, detail


def _mask_value(v):
    return None if not v else '****' + (v[-4:] if len(v) > 4 else '')


def mask(cfg):
    out = json.loads(json.dumps(cfg))
    for k in SECRETS:
        if out.get('config', {}).get(k):
            out['config'][k] = _mask_value(out['config'][k])
    return out


def _current():
    return settings.get('notify') or {'type': None, 'enabled': False, 'config': {},
                                      'events': list(DEFAULT_EVENTS), 'last_test': None}


def get_notify(req):
    return 200, mask(_current())


def put_notify(req):
    b = req.json
    if not isinstance(b, dict) or set(b) - {'type', 'enabled', 'config', 'events', 'last_test'}:
        raise HttpError(400, 'unknown field')
    t = b.get('type')
    if t not in CONFIG_KEYS:
        raise HttpError(400, 'type must be telegram, discord or ntfy')
    enabled = b.get('enabled', False)
    if not isinstance(enabled, bool):
        raise HttpError(400, 'enabled must be true or false')
    given = b.get('config') or {}
    if not isinstance(given, dict) or set(given) - set(CONFIG_KEYS[t]):
        raise HttpError(400, f'config keys for {t}: ' + ', '.join(CONFIG_KEYS[t]))
    cur = _current()
    old = cur['config'] if cur.get('type') == t else {}
    config = {}
    for k, required in CONFIG_KEYS[t].items():
        v = given.get(k)
        if k in SECRETS and (v is None or v == _mask_value(old.get(k))):
            v = old.get(k)
        if k == 'chat_id' and isinstance(v, int) and not isinstance(v, bool):
            v = str(v)
        if v in (None, ''):
            if required:
                raise HttpError(400, f'{k} required')
            continue
        if not isinstance(v, str):
            raise HttpError(400, f'{k} must be a string')
        if k in URL_KEYS:
            u = urllib.parse.urlparse(v)
            if u.scheme not in ('http', 'https') or not u.netloc:
                raise HttpError(400, f'{k} must be an http(s) URL')
        config[k] = v
    ev = b.get('events', list(DEFAULT_EVENTS))
    if not isinstance(ev, list) or not set(ev) <= set(EVENTS):
        raise HttpError(400, 'events must be a list of: ' + ', '.join(EVENTS))
    value = {'type': t, 'enabled': enabled, 'config': config, 'events': ev, 'last_test': cur.get('last_test')}
    settings.put('notify', value)
    return 200, mask(value)


def test_notify(req):
    cur = settings.get('notify')
    if not cur or not cur.get('type'):
        raise HttpError(400, 'no channel configured')
    ok, status, detail = send(cur, 'Relume test message')
    cur['last_test'] = {'at': db.utcnow(), 'ok': ok, 'detail': '' if ok else f'{status}: {detail[:500]}'}
    settings.put('notify', cur)
    return 200, {'ok': ok, 'status': status, 'detail': detail[:500]}


ROUTES = [
    ('GET', '/api/notify', get_notify),
    ('PUT', '/api/notify', put_notify),
    ('POST', '/api/notify/test', test_notify),
]
