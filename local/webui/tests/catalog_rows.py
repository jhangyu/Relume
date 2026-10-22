"""Shared rows and fakes for the catalog tests (plain SQL, no routes)."""
import json
import subprocess
import unittest
from unittest import mock

from webui import catalog, db, events, settings, upstream
from webui.tests import helpers

DOWNLOAD = {"kind": "apkmirror", "type": "apk", "arch": None, "dpi": None, "minver": None, "url": None}
VARIANTS = [
    {"name": "universal", "abi": "universal", "download": dict(DOWNLOAD), "exclude_splits": None, "strip_libs": None},
    {"name": "arm64-v8a", "abi": "arm64-v8a", "download": dict(DOWNLOAD), "exclude_splits": None, "strip_libs": "arm64-v8a"},
]


def add_basic(c):
    """upstreams 1 cli, 2 patches stable, 3 patches prerelease; sources morphe, morphe-beta;
    groups 1 Morphe, 2 Beta; items 1 YouTube (g1), 2 Reddit (g1), 3 YouTube beta (g2)."""
    c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (1,'cli','MorpheApp/morphe-desktop','stable')")
    c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (2,'patches','MorpheApp/morphe-patches','stable')")
    c.execute("INSERT INTO upstream(id, kind, repo, channel) VALUES (3,'patches','MorpheApp/morphe-patches','prerelease')")
    c.execute("INSERT INTO source(id, name, upstream_id, cli_upstream_id) VALUES ('morphe','Morphe',2,1),('morphe-beta','Morphe Beta',3,1)")
    c.execute("INSERT INTO app(package, display_name) VALUES ('com.google.android.youtube','YouTube'),('com.reddit.frontpage',NULL)")
    c.execute("INSERT INTO grp(id, name, color, position) VALUES (1,'Morphe',NULL,0),(2,'Beta',NULL,1)")
    v = json.dumps(VARIANTS)
    c.execute("INSERT INTO item(id, source_id, package, key, group_id, position, variants) VALUES (1,'morphe','com.google.android.youtube','youtube',1,0,?)", (v,))
    c.execute("INSERT INTO item(id, source_id, package, key, group_id, position, variants) VALUES (2,'morphe','com.reddit.frontpage','reddit',1,1,?)", (v,))
    c.execute("INSERT INTO item(id, source_id, package, key, group_id, position, variants) VALUES (3,'morphe-beta','com.google.android.youtube','youtube',2,0,?)", (v,))


class FakeBuild:
    """Stands in for webui.build in catalog tests."""

    def __init__(self):
        self.active = {}
        self.forgotten = []

    def item_status(self, ids):
        return {i: {"last_build": None, "last_success": None, "active_job": self.active.get(i)} for i in ids}

    def forget_item(self, item_id):
        self.forgotten.append(item_id)


class FakeHTTP:
    """url -> (status, body object or bytes, headers)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers=None, timeout=30):
        self.calls.append((url, headers or {}))
        st, body, hdr = self.routes.get(url, (404, {}, {}))
        return st, hdr, body if isinstance(body, bytes) else json.dumps(body).encode()


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
    """Temp DB + basic rows + FakeBuild; self.call(handler, json=…, **params)."""

    def setUp(self):
        super().setUp()
        self.build = FakeBuild()
        p = mock.patch.object(catalog, "build", self.build)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(settings, "github_token", return_value="")
        p.start()
        self.addCleanup(p.stop)
        self.emitted = []
        p = mock.patch.object(events, "emit", side_effect=lambda t, pl: self.emitted.append((t, pl)))
        p.start()
        self.addCleanup(p.stop)
        with db.tx() as c:
            add_basic(c)

    def http(self, routes):
        f = FakeHTTP(routes)
        p = mock.patch.object(upstream, "http_get", f)
        p.start()
        self.addCleanup(p.stop)
        return f

    def call(self, handler, json=None, method="POST", **params):
        return handler(helpers.fake_request(method, "/test", json=json, params=params))

    def status_of(self, handler, json=None, method="POST", **params):
        try:
            return self.call(handler, json=json, method=method, **params)[0]
        except Exception as e:  # server.HttpError carries .status
            return getattr(e, "status", repr(e))
