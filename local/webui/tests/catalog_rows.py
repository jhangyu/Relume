"""Shared rows and fakes for the catalog tests (plain SQL, no routes)."""
import json
import subprocess
from unittest import mock

from webui import db, events, settings, upstream
from webui.tests import helpers

DOWNLOAD = {"kind": "apkmirror", "type": "apk", "arch": None, "dpi": None, "minver": None, "url": None}
VARIANTS = [
    {"name": "universal", "abi": "universal", "download": dict(DOWNLOAD), "exclude_splits": None, "strip_libs": None},
    {"name": "arm64-v8a", "abi": "arm64-v8a", "download": dict(DOWNLOAD), "exclude_splits": None, "strip_libs": "arm64-v8a"},
]


def builtin_id(c):
    """The built-in bundle row migration 010 created."""
    return c.execute("SELECT id FROM bundle WHERE origin = 'builtin'").fetchone()[0]


def add_bundle(c, repo, *, origin="official", engine="morphe", host="github", upstream_id=None, asset_regex=None,
               name=None):
    return c.execute("INSERT INTO bundle(origin, host, repo, engine, name, upstream_id, asset_regex) VALUES (?,?,?,?,?,?,?)",
                     (origin, host, repo, engine, name or repo, upstream_id, asset_regex)).lastrowid


def add_item(c, package, bundle_id, *, enabled=1, **cols):
    """An enabled-app row (and its app row); cols = other item columns, variants default VARIANTS."""
    c.execute("INSERT OR IGNORE INTO app(package) VALUES (?)", (package,))
    cols = {"variants": json.dumps(VARIANTS), **cols, "package": package, "bundle_id": bundle_id, "enabled": enabled}
    return c.execute(f"INSERT INTO item({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     tuple(cols.values())).lastrowid


def add_store_app(c, package, name, category=None, icon_url=None):
    c.execute("INSERT INTO store_app(package, name, category, icon_url) VALUES (?,?,?,?)", (package, name, category, icon_url))


def add_basic(c):
    """cli upstream (morphe-desktop stable); built-in bundle (010) and one official bundle without upstream;
    items 1 YouTube (built-in, enabled), 2 Reddit (built-in, enabled), 3 YT Music (official, disabled)."""
    cli = c.execute("INSERT INTO upstream(kind, repo, channel) VALUES ('cli','MorpheApp/morphe-desktop','stable')").lastrowid
    builtin = builtin_id(c)
    official = add_bundle(c, "o/official-patches", name="Official Patches")
    for package, name in (("com.google.android.youtube", "YouTube"), ("com.reddit.frontpage", "Reddit"),
                          ("com.google.android.apps.youtube.music", "YouTube Music")):
        add_store_app(c, package, name)
    items = [add_item(c, "com.google.android.youtube", builtin), add_item(c, "com.reddit.frontpage", builtin),
             add_item(c, "com.google.android.apps.youtube.music", official, enabled=0)]
    assert items == [1, 2, 3], items
    return {"cli": cli, "builtin": builtin, "official": official, "items": items}


class FakeHTTP:
    """url -> (status, body object or bytes, headers[, final_url]); returns upstream.http_get's
    (status, headers, body, final_url), final_url = the request URL unless the route gives one."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers=None, timeout=30, max_bytes=None):
        self.calls.append((url, headers or {}))
        st, body, hdr, *final = self.routes.get(url, (404, {}, {}))
        return st, hdr, body if isinstance(body, bytes) else json.dumps(body).encode(), final[0] if final else url


class FakeLog:
    """jobs log stand-in: runs children synchronously, records argv and lines."""

    def __init__(self):
        self.lines = []
        self.runs = []

    def line(self, text):
        self.lines.append(text)

    def run(self, argv, cwd, env, timeout=None, on_line=None):
        self.runs.append(list(argv))
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        for text in (p.stdout + p.stderr).splitlines():
            self.lines.append(text)
            if on_line:
                on_line(text)
        return p.returncode


class CatalogCase(helpers.DataTestCase):
    """Temp DB + basic rows (self.basic = add_basic's result); self.call(handler, json=…, **params)."""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(settings, "github_token", return_value=""))
        self.emitted = []
        self.enterContext(mock.patch.object(events, "emit", side_effect=lambda t, pl: self.emitted.append((t, pl))))
        with db.tx() as c:
            self.basic = add_basic(c)

    def http(self, routes):
        f = FakeHTTP(routes)
        self.enterContext(mock.patch.object(upstream, "http_get", f))
        return f

    def call(self, handler, json=None, method="POST", query=None, **params):
        return handler(helpers.fake_request(method, "/test", json=json, params=params, query=query))

    def status_of(self, handler, json=None, method="POST", query=None, **params):
        try:
            return self.call(handler, json=json, method=method, query=query, **params)[0]
        except Exception as e:  # server.HttpError carries .status
            return getattr(e, "status", repr(e))
