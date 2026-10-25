import importlib
import ipaddress
import json
import mimetypes
import os
import pathlib
import re
import sqlite3
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db

VERSION = os.environ.get('RELUME_VERSION', 'dev')
STARTED_AT = db.utcnow()
MODULES = ('catalog', 'upstream', 'projects', 'build', 'devices', 'ota', 'notify')
CORE = ('auth', 'jobs', 'scheduler', 'settings')
STATIC = pathlib.Path(__file__).with_name('static')
MUTATING = {'POST', 'PUT', 'PATCH', 'DELETE'}
MAX_BODY = 1 << 20
MAX_DRAIN = 16 << 20  # bodies up to this size are read and discarded before a 413
CTYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript',
          '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json'}
PAGE = re.compile(r'^/([a-z]+\.html)$')
ASSET = re.compile(r'^/static/([A-Za-z0-9_-]+\.(?:css|js|svg|png|webmanifest))$')
RANGE = re.compile(r'bytes=(\d*)-(\d*)')

SLUG = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._-]*$')
PACKAGE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$')
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
LABEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{0,62}$')


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class FileResponse:
    """download_name None = inline (e.g. icons); cache_seconds > 0 adds Cache-Control max-age;
    etag (unquoted) enables If-Range; headers = extra response headers."""
    def __init__(self, path, download_name=None, cache_seconds=0, etag=None, headers=None):
        self.path, self.download_name, self.cache_seconds = pathlib.Path(path), download_name, cache_seconds
        self.etag, self.headers = etag, dict(headers or {})


class StreamResponse:
    def __init__(self, write_fn, download_name):
        self.write_fn, self.download_name = write_fn, download_name


class SSEResponse:
    def __init__(self, iterator):
        self.iterator = iterator


class Request:
    def __init__(self, method, path, params, query, headers):
        self.method, self.path, self.params, self.query, self.headers = method, path, params, query, headers
        self.json = {}
        self.session = None
        self.device_id = None


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def int_list(v, field):
    if not (isinstance(v, list) and all(is_int(x) for x in v) and len(set(map(str, v))) == len(v)):
        raise HttpError(400, f'invalid {field}')
    return v


def validate_slug(v, field='value'):
    if not isinstance(v, str) or not SLUG.match(v):
        raise HttpError(400, f'invalid {field}')
    return v


def validate_package(v, field='package'):
    if not isinstance(v, str) or not PACKAGE.match(v):
        raise HttpError(400, f'invalid {field}')
    return v


def validate_repo(v, field='repo'):
    if not isinstance(v, str) or not REPO.match(v):
        raise HttpError(400, f'invalid {field}')
    return v


def validate_host(v, field='host'):
    if isinstance(v, str):
        try:
            ipaddress.IPv4Address(v)
            return v
        except ValueError:
            labels = v.split('.')
            if len(v) <= 253 and all(LABEL.match(x) for x in labels):
                return v
    raise HttpError(400, f'invalid {field}')


def validate_port(v, field='port'):
    if not is_int(v) or not 1 <= v <= 65535:
        raise HttpError(400, f'invalid {field}')
    return v


def safe_remote(v):
    s = re.sub(r'[^A-Za-z0-9._-]', '_', str(v))
    return '_' + s[1:] if s[:1] in ('.', '-') else s


def load_modules(names=MODULES):
    return [importlib.import_module('webui.' + n) for n in names]


def _compile(pattern):
    return re.compile('^' + re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', pattern) + '$')


def byte_range(header, size):
    """One `Range: bytes=` spec -> inclusive (start, end); None when unparsable, multi-range or unsatisfiable."""
    m = RANGE.fullmatch(header.strip())
    if not m or not (m[1] or m[2]):
        return None
    if not m[1]:
        n = int(m[2])
        return (max(0, size - n), size - 1) if 0 < n and size else None
    start, end = int(m[1]), int(m[2]) if m[2] else size - 1
    return (start, min(end, size - 1)) if start <= end and start < size else None


def _attachment(name):
    return 'attachment; filename="' + re.sub(r'["\\\r\n]', '_', name) + '"'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):  # ponytail: no access log, server log stays readable
        pass

    def do_GET(self): self._dispatch('GET')
    def do_POST(self): self._dispatch('POST')
    def do_PUT(self): self._dispatch('PUT')
    def do_PATCH(self): self._dispatch('PATCH')
    def do_DELETE(self): self._dispatch('DELETE')

    def _dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        try:
            if not url.path.startswith('/api/'):
                return self._static(url.path)
            self._send(self._api(method, url))
        except HttpError as e:
            self._json(e.status, {'error': e.message})
        except sqlite3.OperationalError as e:
            if 'locked' in str(e):
                self._json(503, {'error': 'database busy'})
            else:
                traceback.print_exc()
                self._json(500, {'error': 'internal error'})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self._json(500, {'error': 'internal error'})

    def _api(self, method, url):
        from . import auth  # lazy: auth imports HttpError from this module
        found = None
        path_known = False
        for m, rx, pattern, fn in self.server.routes:
            mt = rx.match(url.path)
            if mt:
                path_known = True
                if m == method:
                    found = (pattern, fn, mt)
                    break
        if not found:
            raise HttpError(405 if path_known else 404, 'method not allowed' if path_known else 'not found')
        pattern, fn, mt = found
        params = {k: int(v) if re.fullmatch(r'-?\d+', v) else urllib.parse.unquote(v)
                  for k, v in mt.groupdict().items()}
        query = dict(urllib.parse.parse_qsl(url.query))
        req = Request(method, url.path, params, query, self.headers)
        if method in MUTATING:
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise HttpError(415, 'content type must be application/json')
            origin = self.headers.get('Origin')
            if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
                raise HttpError(403, 'origin mismatch')
            req.json = self._read_json_body()
        if (method, pattern) not in auth.PUBLIC:
            req.session, req.device_id = auth.authenticate(self.headers)
            if req.session is None and req.device_id is None:
                raise HttpError(401, 'login required')
            if pattern == '/api/me' or pattern.startswith('/api/me/'):
                if req.device_id is None:
                    raise HttpError(403, 'device token required')
            elif req.device_id is not None and (method, pattern) in auth.SESSION_ONLY:
                raise HttpError(403, 'session required')
        return fn(req)

    def _read_json_body(self):
        cl = self.headers.get('Content-Length') or '0'
        if not (cl.isascii() and cl.isdigit()):  # also rejects '-1': rfile.read(-1) would read until the client closes
            self.close_connection = True
            raise HttpError(400, 'invalid content-length')
        n = int(cl)
        if n > MAX_BODY:
            self.close_connection = True
            if n <= MAX_DRAIN:  # let the client finish sending, else it may see a broken pipe, not the 413
                while n > 0:
                    got = self.rfile.read(min(n, 1 << 16))
                    if not got:
                        break
                    n -= len(got)
            raise HttpError(413, 'body too large')
        raw = self.rfile.read(n) if n else b''
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            raise HttpError(400, 'invalid json')
        if not isinstance(body, dict):  # every API body is an object
            raise HttpError(400, 'body must be a JSON object')
        return body

    def _json(self, status, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send(self, res):
        if isinstance(res, tuple):
            return self._json(*res)
        if isinstance(res, FileResponse):
            return self._file(res)
        if isinstance(res, StreamResponse):
            self.close_connection = True
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', _attachment(res.download_name))
            self.send_header('Connection', 'close')
            self.end_headers()
            res.write_fn(self.wfile)
            return
        if isinstance(res, SSEResponse):
            self.close_connection = True
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()
            for chunk in res.iterator:
                self.wfile.write(chunk)
                self.wfile.flush()
            return
        raise TypeError(f'bad handler result {type(res).__name__}')

    def _file(self, res):
        with open(res.path, 'rb') as f:
            size = os.fstat(f.fileno()).st_size
            span, status = (0, size - 1), 200
            want = self.headers.get('Range')
            if want and (self.headers.get('If-Range') is None or self.headers['If-Range'].strip() == f'"{res.etag}"'):
                span = byte_range(want, size)
                if span is None:
                    return self._json(416, {'error': f'range {want!r} not satisfiable for {size} bytes'},
                                      {'Content-Range': f'bytes */{size}'})
                status = 206
            length = span[1] - span[0] + 1
            self.send_response(status)
            self.send_header('Content-Type', mimetypes.guess_type(res.path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(length))
            self.send_header('Accept-Ranges', 'bytes')
            if status == 206:
                self.send_header('Content-Range', f'bytes {span[0]}-{span[1]}/{size}')
            if res.etag:
                self.send_header('ETag', f'"{res.etag}"')
            if res.download_name:
                self.send_header('Content-Disposition', _attachment(res.download_name))
            if res.cache_seconds:
                self.send_header('Cache-Control', f'max-age={int(res.cache_seconds)}')
            for k, v in res.headers.items():
                self.send_header(k, v)
            self.end_headers()
            f.seek(span[0])
            while length > 0:
                chunk = f.read(min(length, 1 << 20))
                if not chunk:
                    self.close_connection = True
                    raise OSError(f'{res.path} shrank while sending: {length} of {size} bytes missing')
                self.wfile.write(chunk)
                length -= len(chunk)

    def _static(self, path):
        from . import auth  # lazy: auth imports HttpError from this module
        if path == '/':
            name, public = 'index.html', False
        elif ASSET.match(path):
            name, public = ASSET.match(path).group(1), True
        elif PAGE.match(path):
            name = PAGE.match(path).group(1)
            public = name == 'login.html'
        else:
            raise HttpError(404, 'not found')
        f = STATIC / name
        if not f.is_file():
            raise HttpError(404, 'not found')
        if not public and not auth.session_token(self.headers.get('Cookie')):
            self.send_response(302)
            self.send_header('Location', '/login.html')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', CTYPES[f.suffix])
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)


def make_server(mods, host='0.0.0.0', port=47000):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    routes = [(m, _compile(p), p, fn)
              for mod in load_modules(CORE) + list(mods) for (m, p, fn) in getattr(mod, 'ROUTES', [])]
    # literal segments win over {param} ones (PUT /api/groups/order before PUT /api/groups/{id})
    srv.routes = sorted(routes, key=lambda r: r[2].count('{'))
    return srv
