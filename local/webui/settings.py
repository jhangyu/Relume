import datetime
import importlib
import json
import os
import pathlib
import platform
import re
import shutil
import threading
import time
import urllib.error
import urllib.request

from . import db

TOKEN_PATH = pathlib.Path('/run/secrets/github_token')
RATE_URL = 'https://api.github.com/rate_limit'
_gh_cache = {}
_gh_lock = threading.Lock()
LIMITS = {'job_history_count': (1, 100000), 'artifact_retention': (1, 100)}
TOKEN_RE = re.compile(r'[A-Za-z0-9_]{0,255}\Z')  # GitHub classic and fine-grained tokens; '' clears


def get(key, default=None):
    r = db.conn().execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(r['value']) if r else default


def put(key, value):
    with db.tx() as c:
        c.execute('INSERT INTO settings(key, value) VALUES (?, ?) '
                  'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))


def ui_token_path():
    """Token entered on the Settings page. DATA sits in the root-only /private tmpfs (compose), and the
    secrets dir is 0700 / the file 0600, so the isolated runner uid cannot read it."""
    return db.DATA / 'secrets' / 'github_token'


def _read(p):
    try:
        return p.read_text().strip() if p.is_file() else ''
    except OSError:
        return ''


def _token_source():
    """(token, source): the UI-stored token first, else the optional mounted file TOKEN_PATH."""
    t = _read(ui_token_path())
    if t:
        return t, 'ui'
    return _read(TOKEN_PATH), 'file' if TOKEN_PATH.is_file() else None


def github_token():
    return _token_source()[0]


def set_github_token(token):
    """'' deletes the UI token (the mounted file, if any, applies again)."""
    f = ui_token_path()
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    f.parent.chmod(0o700)
    if not token:
        f.unlink(missing_ok=True)
    else:
        tmp = f.with_name(f.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as fh:
            fh.write(token + '\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, f)
    with _gh_lock:
        _gh_cache.clear()


def check_token_file():
    # /dev/null = compose default when RV_GITHUB_TOKEN_FILE is unset (the file is optional): no warning
    if TOKEN_PATH.exists() and not TOKEN_PATH.is_file() and not TOKEN_PATH.samefile(os.devnull):
        print(f'github token path is not a file: {TOKEN_PATH}', flush=True)


def fetch_rate_limit(token):
    h = {'Accept': 'application/vnd.github+json', 'User-Agent': 'patchbay'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    try:
        with urllib.request.urlopen(urllib.request.Request(RATE_URL, headers=h), timeout=10) as r:
            return r.status, json.load(r), r.headers
    except urllib.error.HTTPError as e:
        return e.code, {}, e.headers


def github_status(recheck=False):
    with _gh_lock:
        hit = _gh_cache.get('v')
        if hit and not recheck and time.monotonic() - _gh_cache['at'] < 300:
            return hit
    token, source = _token_source()
    present = source is not None
    v = dict.fromkeys(('limit', 'remaining', 'reset_at', 'expires_at', 'scopes'))
    v.update(path=str(TOKEN_PATH), source=source, token_hint='****' + token[-4:] if token else None)
    try:
        status, body, headers = fetch_rate_limit(token)
    except OSError as e:
        return {**v, 'state': 'absent' if not present else 'unknown', 'error': str(e)}
    core = (body or {}).get('resources', {}).get('core', {})
    v['state'] = ('absent' if not present else 'rejected' if status == 401
                  else 'unauthenticated' if not token else 'authenticated')
    v['limit'], v['remaining'] = core.get('limit'), core.get('remaining')
    if core.get('reset'):
        v['reset_at'] = datetime.datetime.fromtimestamp(core['reset'], datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    v['expires_at'] = headers.get('github-authentication-token-expiration') if headers else None
    v['scopes'] = headers.get('X-OAuth-Scopes') if headers else None
    with _gh_lock:
        _gh_cache.update(v=v, at=time.monotonic())
    return v


def _size(p):
    return sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) if p.exists() else 0


def server_info():
    d = db.DATA
    u = shutil.disk_usage(d)
    arts = d / 'artifacts'
    return {'arch': platform.machine(), 'tz': os.environ.get('TZ') or 'UTC', 'now': db.utcnow(),
            'network_mode': os.environ.get('RV_NETWORK_MODE', ''),
            'disk': {'total': u.total, 'free': u.free, 'artifacts': _size(arts), 'apk_cache': _size(d / 'apk-cache'),
                     'upstream': _size(d / 'upstream'), 'logs': _size(d / 'logs'),
                     'artifacts_count': sum(1 for _ in arts.rglob('*.apk')) if arts.exists() else 0}}


def _payload(recheck=False):
    return {'job_history_count': get('job_history_count', 500), 'base_url': get('base_url', ''),
            'artifact_retention': get('artifact_retention', 3),
            'github': github_status(recheck), 'server': server_info()}


def health(req):
    from .server import STARTED_AT, VERSION
    return 200, {'ok': True, 'app': 'Patchbay', 'version': VERSION, 'started_at': STARTED_AT}


def get_route(req):
    return 200, _payload(req.query.get('recheck') == '1')


def put_route(req):
    from .server import HttpError
    b = req.json
    if not isinstance(b, dict) or set(b) - {'job_history_count', 'base_url', 'artifact_retention', 'github_token'}:
        raise HttpError(400, 'unknown setting')
    if 'github_token' in b and not (isinstance(b['github_token'], str) and TOKEN_RE.match(b['github_token'])):
        raise HttpError(400, 'invalid github_token')
    for k, (lo, hi) in LIMITS.items():
        if k in b and (isinstance(b[k], bool) or not isinstance(b[k], int) or not lo <= b[k] <= hi):
            raise HttpError(400, f'invalid {k}')
    if 'base_url' in b and not (b['base_url'] == '' or (isinstance(b['base_url'], str) and re.match(r'^https?://\S+$', b['base_url']))):
        raise HttpError(400, 'invalid base_url')
    old_keep = get('artifact_retention', 3)
    for k, v in b.items():
        if k == 'github_token':
            set_github_token(v)   # write-only: never stored in the settings table, never returned
        else:
            put(k, v.rstrip('/') if k == 'base_url' else v)
    if 'artifact_retention' in b and b['artifact_retention'] != old_keep:
        importlib.import_module('webui.build').run_retention()
    return 200, _payload()


ROUTES = [
    ('GET', '/api/health', health),
    ('GET', '/api/settings', get_route),
    ('PUT', '/api/settings', put_route),
]
