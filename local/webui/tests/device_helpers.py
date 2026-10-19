"""Shared helpers for the device (P5) and ci (P7) tests. Not a test module."""
import json
import os
import pathlib
import sys
import tempfile
import threading
import types

from webui import db, devices

FIXTURES = pathlib.Path(__file__).parent / 'fixtures' / 'device'
OTA_DEFAULTS = {'retries': 0, 'mode': 'normal', 'package': None, 'notify': True}


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(',', ':'))


def req(params=None, body=None, query=None, method='POST', path='/'):
    return types.SimpleNamespace(method=method, path=path, params=params or {}, query=query or {},
                                 headers={}, json={} if body is None else body, session=None)


class FakeLog:
    def __init__(self):
        self.lines = []

    def line(self, text):
        self.lines.append(str(text))

    def run(self, argv, cwd=None, env=None, timeout=None, on_line=None):
        raise AssertionError('log.run is not used by device handlers')


def add_job(kind='ota', target=None, options=None, trigger='manual', status='running'):
    opts = dict(OTA_DEFAULTS, **(options or {})) if kind == 'ota' else (options or {})
    with db.tx() as c:
        return c.execute('INSERT INTO jobs(kind,status,trigger,target,options,created_at) VALUES (?,?,?,?,?,?)',
                         (kind, status, trigger, canon(target or {}), canon(opts), db.utcnow())).lastrowid


def job(jid, target=None, options=None, trigger='manual', kind='ota'):
    opts = dict(OTA_DEFAULTS, **(options or {})) if kind == 'ota' else (options or {})
    return types.SimpleNamespace(id=jid, kind=kind, target=target or {}, options=opts, trigger=trigger,
                                 schedule_id=None, cancel_event=threading.Event())


def add_group(gid, position):
    with db.tx() as c:
        c.execute('INSERT INTO grp(id,name,color,position) VALUES (?,?,NULL,?)', (gid, f'g{gid}', position))


def add_catalog_item(item_id, package, source_id='src', group_id=None, position=0, enabled=1, key=None):
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO upstream(id,kind,repo,channel,tag,asset_regex,latest_tag,downloaded_tag) "
                  "VALUES (1,'patches','o/p','stable',NULL,'\\.mpp$','v1','v1')")
        c.execute("INSERT OR IGNORE INTO upstream(id,kind,repo,channel,tag,asset_regex,latest_tag,downloaded_tag) "
                  "VALUES (2,'cli','o/cli','stable',NULL,'\\.jar$','v1','v1')")
        c.execute("INSERT OR IGNORE INTO source(id,name,upstream_id,cli_upstream_id,engine,keystore) "
                  "VALUES (?,?,1,2,'morphe','morphe.keystore')", (source_id, source_id))
        c.execute('INSERT OR IGNORE INTO app(package,display_name) VALUES (?,?)', (package, package))
        c.execute('INSERT INTO item(id,source_id,package,key,display_name,group_id,position,enabled,version_mode,'
                  "version_pin,allow_nearest,variants,patch_overrides,engine_options) "
                  "VALUES (?,?,?,?,NULL,?,?,?,'auto',NULL,0,'[]','{}',NULL)",
                  (item_id, source_id, package, key or f'k{item_id}', group_id, position, enabled))


def add_device(host='192.168.1.20', port=37011, serial='SER1', abi='arm64-v8a,armeabi-v7a', after_build=0,
               mdns_name=None):
    with db.tx() as c:
        return c.execute('INSERT INTO devices(name,serial,mdns_name,host,connect_port,model,abi_list,sdk,'
                         'android_release,ota_after_build,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',
                         ('Pixel', serial, mdns_name, host, port, 'Pixel', abi, 35, '15', after_build)).lastrowid


def assign(device_id, *item_ids):
    with db.tx() as c:
        c.executemany('INSERT INTO device_assignments(device_id,item_id) VALUES (?,?)',
                      [(device_id, i) for i in item_ids])


class FakeAdb:
    """Points devices.ADB at fixtures/device/fake-adb for one test; restored on cleanup."""

    def __init__(self, tc):
        tmp = tempfile.TemporaryDirectory()
        tc.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        (self.dir / 'rules.json').write_text('[]')
        old_adb, old_env = devices.ADB, os.environ.get('FAKE_ADB_DIR')
        devices.ADB = [sys.executable, str(FIXTURES / 'fake-adb')]
        os.environ['FAKE_ADB_DIR'] = str(self.dir)

        def restore():
            devices.ADB = old_adb
            if old_env is None:
                os.environ.pop('FAKE_ADB_DIR', None)
            else:
                os.environ['FAKE_ADB_DIR'] = old_env
        tc.addCleanup(restore)

    def rule(self, *match, out='', rc=0, times=None, hang=False, delay=0):
        p = self.dir / 'rules.json'
        rules = json.loads(p.read_text())
        rules.append({'match': list(match), 'out': out, 'rc': rc, 'times': times, 'hang': hang, 'delay': delay})
        p.write_text(json.dumps(rules))
        return self

    def calls(self):
        p = self.dir / 'argv.log'
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
