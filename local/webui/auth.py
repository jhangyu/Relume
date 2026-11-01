import datetime
import hashlib
import hmac
import http.cookies
import math
import os
import re
import secrets
import threading
import time

from . import catalog, db, ota, settings
from .server import HttpError

COOKIE = 'relume_session'
PUBLIC = {('POST', '/api/auth/login'), ('POST', '/api/auth/setup'),
          ('POST', '/api/auth/device'), ('GET', '/api/auth/state'), ('GET', '/api/health')}
# a device token gets 403 here: these change the account or revoke another phone
SESSION_ONLY = {('POST', '/api/auth/password'), ('POST', '/api/auth/logout'), ('PUT', '/api/settings'),
                ('PUT', '/api/notify'), ('DELETE', '/api/devices/{id}'), ('DELETE', '/api/devices/{id}/token'),
                ('GET', '/api/legacy-sign-ins'), ('DELETE', '/api/legacy-sign-ins/{id}')}
# v1.2.0 RelumeApi.kt: me/sync/plan/receipt/logout, assignments (own row, answered as a no-op), the cards of the
# install screens (apps, marketplace, projects, icons), signer accept and the two download routes
LEGACY_ALLOWED = {
    ('GET', '/api/me'), ('POST', '/api/me/sync'), ('GET', '/api/me/plan'), ('POST', '/api/me/installs'),
    ('POST', '/api/me/logout'), ('PUT', '/api/devices/{id}/assignments'), ('GET', '/api/projects'),
    ('POST', '/api/projects/{id}/signer/accept'), ('GET', '/api/projects/{id}/apks/{file}'),
    ('GET', '/api/projects/{id}/icon'), ('GET', '/api/artifacts/{id}/download'), ('GET', '/api/apps'),
    ('GET', '/api/marketplace'), ('GET', '/api/marketplace/{pkg}'), ('GET', '/api/apps/{pkg}/icon')}
LEGACY_DENIED_MSG = 'this Relume app version cannot change device settings: update the app'
DEVICE_NAME_MAX = 64
MAX_FAILS, WINDOW, LOCK = 5, 300, 300
REMEMBER, SHORT = 30 * 86400, 12 * 3600
ADMIN_PASSWORD_ENV = 'RELUME_ADMIN_PASSWORD'
SCRYPT_N = 2 ** 14
MIN_PASSWORD = 10
USERNAME_RE = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
_clock = time.monotonic
_fails = []
_state = {'locked_until': 0.0}
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


def account_exists():
    return settings.get('admin_username') is not None and settings.get('admin_password_hash') is not None


def apply_env_password():
    """Boot step: RELUME_ADMIN_PASSWORD (non-empty) replaces the admin password and signs out every cookie session.
    Device tokens stay. Without an account the variable is reported and ignored: setup needs a username, which the
    variable does not carry. The password is never printed."""
    pw = os.environ.get(ADMIN_PASSWORD_ENV)
    if not pw:
        return
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f'{ADMIN_PASSWORD_ENV} must have at least {MIN_PASSWORD} characters, got {len(pw)}')
    if not account_exists():
        print(f'{ADMIN_PASSWORD_ENV} ignored: no admin account yet, create it on the setup page first', flush=True)
        return
    with db.tx() as c:  # one transaction: the new hash and the sign-out of old sessions happen together
        settings.put('admin_password_hash', hash_password(pw), c=c)
        c.execute('DELETE FROM sessions')
    print(f'admin password set from {ADMIN_PASSWORD_ENV}, all sessions signed out', flush=True)


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


def legacy_refused(device_id, method, pattern):
    """A legacy token (companion up to v1.2.0, no device_uid) reaches only what that app's sign-in, background
    check, install/upgrade, signer accept and logout flows use: LEGACY_ALLOWED. Everything else is 403."""
    if (method, pattern) in LEGACY_ALLOWED:
        return False
    row = db.conn().execute('SELECT legacy FROM devices WHERE id=?', (device_id,)).fetchone()
    return bool(row and row['legacy'])


def client_limits():
    """Rules the server owns and the clients must not copy (web pages, companion). Public: nothing secret."""
    return {'min_password': MIN_PASSWORD, 'abis': list(catalog.ABIS),
            'engines': {name: {'kind': e['kind'], 'max_sigbypass': e.get('max_sigbypass')}
                        for name, e in catalog.ENGINES.items()}}


def auth_state(req):
    return 200, {'setup_required': not account_exists(), 'limits': client_limits()}


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
            settings.put(k, v, c=c)
    _, cookie = create_session(bool(req.json.get('remember', True)))
    return 200, {'ok': True}, {'Set-Cookie': cookie}


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


def register_device(req):
    """Phone login: same credential check and lockout as login, returns a device token once, sets no cookie.
    A phone that signs in again (same device_uid) keeps its row, name, assignments and history: only the token
    and the hardware facts change."""
    d = req.json.get('device')
    facts = ota.parse_device_facts(d)
    name = d.get('name')
    if not isinstance(name, str) or not 0 < len(name.strip()) <= DEVICE_NAME_MAX:
        raise HttpError(400, f'device.name must be 1-{DEVICE_NAME_MAX} characters, got {name!r}')
    denied = _check_credentials(req)
    if denied:
        return denied
    token = secrets.token_urlsafe(32)
    uid, name, hw = facts.uid, name.strip(), (facts.model, facts.sdk, facts.android_release, facts.abi_list)
    with db.tx() as c:
        known = uid and c.execute('SELECT id FROM devices WHERE device_uid=?', (uid,)).fetchone()
        adb = []   # a known phone is never offered an ADB takeover: adopting would delete its own row
        if known:
            did = known['id']
            c.execute('UPDATE devices SET token_hash=?, model=?, sdk=?, android_release=?, abi_list=? WHERE id=?',
                      (token_hash(token), *hw, did))
        elif uid is None:   # companion up to v1.2.0: hidden install-only row, no ADB takeover (it would split on every sign-in)
            did = c.execute("INSERT INTO devices(name, kind, legacy, token_hash, model, sdk, android_release, abi_list) "
                            "VALUES (?, 'app', 1, ?, ?, ?, ?, ?)", (name, token_hash(token), *hw)).lastrowid
        else:
            did = c.execute("INSERT INTO devices(device_uid, name, kind, token_hash, model, sdk, android_release, "
                            "abi_list) VALUES (?, ?, 'app', ?, ?, ?, ?, ?)",
                            (uid, name, token_hash(token), *hw)).lastrowid
            adb = [dict(r) for r in c.execute("SELECT id, name, model FROM devices WHERE kind='adb' ORDER BY id")]
    return 200, {'token': token, 'device': ota.device_view_by_id(did), 'adb_devices': adb}


def device_logout(req):
    """A legacy row has no uid to be found again by: signing out deletes it, a normal row only loses its token."""
    with db.tx() as c:
        if not c.execute('DELETE FROM devices WHERE id=? AND legacy=1', (req.device_id,)).rowcount:
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
    return 200, {'device': ota.device_view_by_id(did)}


def list_legacy_sign_ins(req):
    rows = db.conn().execute('SELECT id, name, model, android_release, created_at, last_seen_at FROM devices '
                             'WHERE legacy=1 ORDER BY id').fetchall()
    return 200, {'legacy_sign_ins': [dict(r) for r in rows]}


def revoke_legacy_sign_in(req):
    with db.tx() as c:
        if not c.execute('DELETE FROM devices WHERE id=? AND legacy=1', (req.params['id'],)).rowcount:
            raise HttpError(404, f"legacy sign-in {req.params['id']} not found")
    return 200, {'deleted': req.params['id']}


def logout(req):
    with db.tx() as c:
        c.execute('DELETE FROM sessions WHERE token=?', (req.session,))
    return 200, {'ok': True}, {'Set-Cookie': f'{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'}


def change_password(req):
    old, new = req.json.get('old'), req.json.get('new')
    if not isinstance(new, str) or len(new) < MIN_PASSWORD:
        raise HttpError(400, f'new password must have at least {MIN_PASSWORD} characters')
    if not isinstance(old, str) or not verify_password(old, settings.get('admin_password_hash')):
        raise HttpError(403, 'wrong password')
    set_password(new)
    with db.tx() as c:
        n = c.execute('DELETE FROM sessions WHERE token != ?', (req.session,)).rowcount
    return 200, {'signed_out': n}


ROUTES = [
    ('GET', '/api/auth/state', auth_state),
    ('POST', '/api/auth/setup', setup),
    ('POST', '/api/auth/login', login),
    ('POST', '/api/auth/logout', logout),
    ('POST', '/api/auth/password', change_password),
    ('POST', '/api/auth/device', register_device),
    ('POST', '/api/me/logout', device_logout),
    ('DELETE', '/api/devices/{id}/token', revoke_device_token),
    ('GET', '/api/legacy-sign-ins', list_legacy_sign_ins),
    ('DELETE', '/api/legacy-sign-ins/{id}', revoke_legacy_sign_in),
]
