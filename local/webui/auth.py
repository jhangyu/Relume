import datetime
import hashlib
import hmac
import http.cookies
import json
import math
import re
import secrets
import threading
import time

from . import catalog, db, devices, ota, settings
from .server import HttpError, is_int

COOKIE = 'relume_session'
PUBLIC = {('POST', '/api/auth/login'), ('POST', '/api/auth/setup'), ('POST', '/api/auth/reset'),
          ('POST', '/api/auth/device'), ('GET', '/api/auth/state'), ('GET', '/api/health')}
# a device token gets 403 here: these change the account or revoke another phone
SESSION_ONLY = {('POST', '/api/auth/password'), ('POST', '/api/auth/logout'), ('PUT', '/api/settings'),
                ('PUT', '/api/notify'), ('DELETE', '/api/devices/{id}'), ('DELETE', '/api/devices/{id}/token')}
DEVICE_NAME_MAX = 64
MAX_FAILS, WINDOW, LOCK = 5, 300, 300
REMEMBER, SHORT = 30 * 86400, 12 * 3600
RESET_COOLDOWN = 300
SCRYPT_N = 2 ** 14
MIN_PASSWORD = 10
USERNAME_RE = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
_clock = time.monotonic
_fails = []
_state = {'locked_until': 0.0, 'reset_at': None}
_lock = threading.Lock()


def _scrypt(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32).hex()


def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    return f'scrypt${salt.hex()}${_scrypt(pw, salt)}'


def verify_password(pw, stored):
    try:
        _, salt, h = stored.split('$')
        return hmac.compare_digest(_scrypt(pw, bytes.fromhex(salt)), h)
    except (AttributeError, ValueError):
        return False


def set_password(pw):
    settings.put('admin_password_hash', hash_password(pw))


def new_password():
    return secrets.token_urlsafe(15)


def account_exists():
    return settings.get('admin_username') is not None and settings.get('admin_password_hash') is not None


def reset_password():
    """Random new password to stdout (server log), all sessions deleted. Used by the reset endpoint and the CLI."""
    username = settings.get('admin_username')
    if username is None or settings.get('admin_password_hash') is None:
        raise HttpError(409, 'setup required')
    pw = new_password()
    with db.tx() as c:  # one transaction: a failed DELETE cannot leave an unprinted new password
        c.execute('INSERT INTO settings(key, value) VALUES (?, ?) '
                  'ON CONFLICT(key) DO UPDATE SET value=excluded.value', ('admin_password_hash', json.dumps(hash_password(pw))))
        c.execute('DELETE FROM sessions')
    print(f'admin password reset for {username}: {pw}', flush=True)


def reset_lockout():
    with _lock:
        _fails.clear()
        _state['locked_until'] = 0.0


def create_session(remember):
    token = secrets.token_urlsafe(32)
    life = REMEMBER if remember else SHORT
    exp = db.iso(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=life))
    with db.tx() as c:
        c.execute('DELETE FROM sessions WHERE expires_at <= ?', (db.utcnow(),))
        c.execute('INSERT INTO sessions(token, expires_at) VALUES (?, ?)', (token, exp))
    cookie = f'{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/'
    return token, cookie + (f'; Max-Age={life}' if remember else '')


def session_token(cookie_header):
    if not cookie_header:
        return None
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(cookie_header)
    except http.cookies.CookieError:
        return None
    m = jar.get(COOKIE)
    if not m or not m.value:
        return None
    r = db.conn().execute('SELECT token FROM sessions WHERE token=? AND expires_at > ?',
                          (m.value, db.utcnow())).fetchone()
    return r['token'] if r else None


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(headers):
    """(session, device_id), at most one non-None. Only the Bearer scheme selects the token path: a bad token is
    a 401, never a fall-through to the cookie, and never counts toward the login lockout. Any other Authorization
    scheme (e.g. Basic from a reverse proxy) is ignored and the cookie path applies."""
    scheme, _, token = (headers.get('Authorization') or '').strip().partition(' ')
    if scheme.lower() != 'bearer':
        return session_token(headers.get('Cookie')), None
    row = None
    if token.strip():
        row = db.conn().execute('SELECT id FROM devices WHERE token_hash=?', (token_hash(token.strip()),)).fetchone()
    if row is None:
        raise HttpError(401, 'invalid device token')
    return None, row['id']


def auth_state(req):
    return 200, {'setup_required': not account_exists()}


def setup(req):
    username, pw = req.json.get('username'), req.json.get('password')
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise HttpError(400, 'username must be 1-64 characters of A-Z a-z 0-9 . _ -')
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        raise HttpError(400, f'password must have at least {MIN_PASSWORD} characters')
    stored = hash_password(pw)
    with db.tx() as c:  # BEGIN IMMEDIATE: check and write are one atomic step across threads
        have = {r['key'] for r in c.execute("SELECT key FROM settings WHERE key IN ('admin_username', 'admin_password_hash')")}
        if len(have) == 2:
            return 409, {'error': 'already set up'}
        for k, v in (('admin_username', username), ('admin_password_hash', stored)):
            c.execute('INSERT INTO settings(key, value) VALUES (?, ?) '
                      'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, json.dumps(v)))
    _, cookie = create_session(bool(req.json.get('remember', True)))
    return 200, {'ok': True}, {'Set-Cookie': cookie}


def reset(req):
    now = _clock()
    with _lock:
        last = _state['reset_at']
        if last is not None and now - last < RESET_COOLDOWN:
            return 429, {'error': 'cooldown', 'retry_after': math.ceil(RESET_COOLDOWN - (now - last))}
        reset_password()
        _state['reset_at'] = now
    return 202, {'ok': True}


def _check_credentials(req):
    """Username/password check with the shared lockout. None = accepted, else the error response."""
    if not account_exists():
        return 409, {'error': 'setup required'}
    now = _clock()
    with _lock:
        if now < _state['locked_until']:
            return 429, {'error': 'locked', 'retry_after': math.ceil(_state['locked_until'] - now)}
    username, pw = req.json.get('username'), req.json.get('password')
    if not isinstance(pw, str) or not isinstance(username, str):
        raise HttpError(400, 'username and password required')
    # both checks always run, so a wrong username costs the same time as a wrong password
    user_ok = hmac.compare_digest(username.encode(), settings.get('admin_username').encode())
    pw_ok = verify_password(pw, settings.get('admin_password_hash'))
    if user_ok and pw_ok:
        reset_lockout()
        return None
    with _lock:
        _fails[:] = [t for t in _fails if now - t < WINDOW] + [now]
        if len(_fails) >= MAX_FAILS:
            _fails.clear()
            _state['locked_until'] = now + LOCK
            return 429, {'error': 'locked', 'retry_after': LOCK}
        return 401, {'error': 'wrong credentials', 'attempts_left': MAX_FAILS - len(_fails)}


def login(req):
    denied = _check_credentials(req)
    if denied:
        return denied
    _, cookie = create_session(bool(req.json.get('remember', True)))
    return 200, {'ok': True}, {'Set-Cookie': cookie}


def _device_facts(body):
    d = body.get('device')
    if not isinstance(d, dict):
        raise HttpError(400, f'device must be an object, got {type(d).__name__}')
    name, model, sdk, release, abis = (d.get(k) for k in ('name', 'model', 'sdk', 'android_release', 'abi_list'))
    if not isinstance(name, str) or not 0 < len(name.strip()) <= DEVICE_NAME_MAX:
        raise HttpError(400, f'device.name must be 1-{DEVICE_NAME_MAX} characters')
    for field, v in (('model', model), ('android_release', release)):
        if not isinstance(v, str) or not v.strip():
            raise HttpError(400, f'device.{field} must be a non-empty string')
    if not is_int(sdk) or sdk < 1:
        raise HttpError(400, f'device.sdk must be a positive integer, got {sdk!r}')
    if not isinstance(abis, list) or not abis:
        raise HttpError(400, 'device.abi_list must be a non-empty list')
    for i, a in enumerate(abis):
        if a not in catalog.ABIS:
            raise HttpError(400, f'device.abi_list[{i}] must be one of {", ".join(catalog.ABIS)}, got {a!r}')
    return name.strip(), model, sdk, release, ','.join(abis)


def _device_view(device_id):
    return ota._device_view(devices.get(device_id), {})


def register_device(req):
    """Phone login: same credential check and lockout as login, returns a device token once, sets no cookie."""
    facts = _device_facts(req.json)
    denied = _check_credentials(req)
    if denied:
        return denied
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        did = c.execute("INSERT INTO devices(name, kind, token_hash, model, sdk, android_release, abi_list) "
                        "VALUES (?, 'app', ?, ?, ?, ?, ?)", (facts[0], token_hash(token), *facts[1:])).lastrowid
        adb = [dict(r) for r in c.execute("SELECT id, name, model FROM devices WHERE kind='adb' ORDER BY id")]
    return 200, {'token': token, 'device': _device_view(did), 'adb_devices': adb}


def device_logout(req):
    with db.tx() as c:
        c.execute('UPDATE devices SET token_hash=NULL WHERE id=?', (req.device_id,))
    return 200, {'ok': True}


def revoke_device_token(req):
    """Row, assignments and history stay; only the phone's token stops working."""
    did = req.params['id']
    with db.tx() as c:
        row = c.execute('SELECT kind FROM devices WHERE id=?', (did,)).fetchone()
        if row is None:
            raise HttpError(404, 'device not found')
        if row['kind'] != 'app':
            raise HttpError(409, 'not an app device')
        c.execute('UPDATE devices SET token_hash=NULL WHERE id=?', (did,))
    return 200, {'device': _device_view(did)}


def logout(req):
    with db.tx() as c:
        c.execute('DELETE FROM sessions WHERE token=?', (req.session,))
    return 200, {'ok': True}, {'Set-Cookie': f'{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'}


def change_password(req):
    old, new = req.json.get('old'), req.json.get('new')
    if not isinstance(new, str) or len(new) < 10:
        raise HttpError(400, 'new password must have at least 10 characters')
    if not isinstance(old, str) or not verify_password(old, settings.get('admin_password_hash')):
        raise HttpError(403, 'wrong password')
    set_password(new)
    with db.tx() as c:
        n = c.execute('DELETE FROM sessions WHERE token != ?', (req.session,)).rowcount
    return 200, {'signed_out': n}


ROUTES = [
    ('GET', '/api/auth/state', auth_state),
    ('POST', '/api/auth/setup', setup),
    ('POST', '/api/auth/reset', reset),
    ('POST', '/api/auth/login', login),
    ('POST', '/api/auth/logout', logout),
    ('POST', '/api/auth/password', change_password),
    ('POST', '/api/auth/device', register_device),
    ('POST', '/api/me/logout', device_logout),
    ('DELETE', '/api/devices/{id}/token', revoke_device_token),
]
