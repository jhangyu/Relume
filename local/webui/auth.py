import datetime
import hashlib
import hmac
import http.cookies
import math
import secrets
import threading
import time

from . import db, settings
from .server import HttpError

COOKIE = 'relume_session'
PUBLIC = {('POST', '/api/auth/login'), ('GET', '/api/health')}
MAX_FAILS, WINDOW, LOCK = 5, 300, 300
REMEMBER, SHORT = 30 * 86400, 12 * 3600
_fails = []
_state = {'locked_until': 0.0}
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


def ensure_password():
    if settings.get('admin_password_hash') is None:
        pw = new_password()
        set_password(pw)
        print(f'admin password: {pw}', flush=True)


def reset_lockout():
    with _lock:
        _fails.clear()
        _state['locked_until'] = 0.0


def create_session(remember):
    token = secrets.token_urlsafe(32)
    life = REMEMBER if remember else SHORT
    exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=life)).strftime('%Y-%m-%dT%H:%M:%SZ')
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


def login(req):
    now = time.monotonic()
    with _lock:
        if now < _state['locked_until']:
            return 429, {'error': 'locked', 'retry_after': math.ceil(_state['locked_until'] - now)}
    pw = req.json.get('password')
    if not isinstance(pw, str):
        raise HttpError(400, 'password required')
    if verify_password(pw, settings.get('admin_password_hash')):
        reset_lockout()
        _, cookie = create_session(bool(req.json.get('remember', True)))
        return 200, {'ok': True}, {'Set-Cookie': cookie}
    with _lock:
        _fails[:] = [t for t in _fails if now - t < WINDOW] + [now]
        if len(_fails) >= MAX_FAILS:
            _fails.clear()
            _state['locked_until'] = now + LOCK
            return 429, {'error': 'locked', 'retry_after': LOCK}
        return 401, {'error': 'wrong password', 'attempts_left': MAX_FAILS - len(_fails)}


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
    ('POST', '/api/auth/login', login),
    ('POST', '/api/auth/logout', logout),
    ('POST', '/api/auth/password', change_password),
]
