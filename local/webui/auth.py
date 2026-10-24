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

from . import db, settings
from .server import HttpError

COOKIE = 'relume_session'
PUBLIC = {('POST', '/api/auth/login'), ('POST', '/api/auth/setup'), ('POST', '/api/auth/reset'),
          ('GET', '/api/auth/state'), ('GET', '/api/health')}
MAX_FAILS, WINDOW, LOCK = 5, 300, 300
REMEMBER, SHORT = 30 * 86400, 12 * 3600
RESET_COOLDOWN = 300
MIN_PASSWORD = 10
USERNAME_RE = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
_clock = time.monotonic
_fails = []
_state = {'locked_until': 0.0, 'reset_at': None}
_lock = threading.Lock()


def _scrypt(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


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


def login(req):
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
        _, cookie = create_session(bool(req.json.get('remember', True)))
        return 200, {'ok': True}, {'Set-Cookie': cookie}
    with _lock:
        _fails[:] = [t for t in _fails if now - t < WINDOW] + [now]
        if len(_fails) >= MAX_FAILS:
            _fails.clear()
            _state['locked_until'] = now + LOCK
            return 429, {'error': 'locked', 'retry_after': LOCK}
        return 401, {'error': 'wrong credentials', 'attempts_left': MAX_FAILS - len(_fails)}


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
]
