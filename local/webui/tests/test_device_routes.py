from unittest import mock

from webui import db, devices
from webui.server import HttpError
from webui.tests import helpers
from webui.tests.device_helpers import (FIXTURES, FakeAdb, add_app_device, add_catalog_item, add_device, add_job, add_project, assign,
                                        assign_projects, req)

GETPROP = (FIXTURES / 'getprop.txt').read_text()
PAIR_BODY = {'host': '192.168.1.20', 'pair_port': 41000, 'code': '123456', 'connect_port': 37011}
DISC = {'adb-ABC123-x1y2': {'service': 'connect', 'host': '192.168.1.20', 'port': 37011}}


class DeviceRoutesTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.fa = FakeAdb(self)
        self.browse = self.enterContext(mock.patch.object(devices, 'browse', return_value={}))

    def pair_ok_rules(self):
        self.fa.rule('pair', out='Successfully paired to 192.168.1.20:41000 [guid=adb-ABC123]\n')
        self.fa.rule('connect', out='connected to 192.168.1.20:37011\n')
        self.fa.rule('getprop', out=GETPROP)

    def test_pair_ok_upserts(self):
        self.pair_ok_rules()
        st, body = devices.pair(req(body=dict(PAIR_BODY)))
        body = body['device']
        self.assertEqual(st, 200)
        self.assertEqual((body['serial'], body['name'], body['sdk'], body['online']), ('32051JEHN09137', 'Pixel 7a', 35, True))
        self.assertEqual(body['abi_list'], 'arm64-v8a,armeabi-v7a,armeabi')
        self.assertEqual(self.fa.calls(), [['pair', '192.168.1.20:41000', '123456'], ['connect', '192.168.1.20:37011'],
                                           ['-s', '192.168.1.20:37011', 'shell', 'getprop']])

    def test_pair_failed_exit0(self):
        self.fa.rule('pair', out='Failed: Unable to start pairing client.\n', rc=0)
        with self.assertRaises(HttpError) as cm:
            devices.pair(req(body=dict(PAIR_BODY)))
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(cm.exception.message, 'PAIR_FAILED: Failed: Unable to start pairing client.')

    def test_pair_timeouts(self):
        def run(fail_on):
            def fake(args, **kw):
                if args[0] == fail_on or args[-1] == fail_on:
                    raise devices.AdbTimeout(' '.join(args))
                return 0, {'pair': 'Successfully paired\n', 'connect': 'connected to x\n'}.get(args[0], '')
            with mock.patch.object(devices, 'adb', side_effect=fake), self.assertRaises(HttpError) as cm:
                devices.pair(req(body=dict(PAIR_BODY)))
            return cm.exception.status, cm.exception.message
        self.assertEqual(run('pair'), (400, 'PAIR_FAILED: timeout'))
        self.assertEqual(run('getprop'), (409, 'DEVICE_OFFLINE'))

    def test_pair_port_from_mdns(self):
        self.browse.return_value = DISC
        self.pair_ok_rules()
        body = {k: v for k, v in PAIR_BODY.items() if k != 'connect_port'}
        dev = devices.pair(req(body=body))[1]['device']
        self.assertIn(['connect', '192.168.1.20:37011'], self.fa.calls())
        self.assertEqual(dev['mdns_name'], 'adb-ABC123-x1y2')

    def test_pair_no_port_400(self):
        body = {k: v for k, v in PAIR_BODY.items() if k != 'connect_port'}
        with self.assertRaises(HttpError) as cm:
            devices.pair(req(body=body))
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(self.fa.calls(), [])

    def test_repair_keeps_assignments_and_history(self):
        add_catalog_item(1, 'com.google.android.youtube')
        d = add_device(host='192.168.1.9', port=5555, serial='32051JEHN09137')
        assign(d, 1)
        jid = add_job(target={'device_id': d})
        with db.tx() as c:
            c.execute("INSERT INTO ota_installs(job_id,device_id,kind,ref_id,package,result) "
                      "VALUES (?,?,'item',1,'com.google.android.youtube','installed')", (jid, d))
        self.pair_ok_rules()
        dev = devices.pair(req(body=dict(PAIR_BODY)))[1]['device']
        self.assertEqual(dev['id'], d)
        self.assertEqual((dev['host'], dev['connect_port']), ('192.168.1.20', 37011))
        c = db.conn()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM device_assignments WHERE device_id=?', (d,)).fetchone()[0], 1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM ota_installs WHERE device_id=?', (d,)).fetchone()[0], 1)

    def test_invalid_inputs_400(self):
        bad = [('host', '-x'), ('host', '1.2.3.4;x'), ('pair_port', 0), ('pair_port', 65536), ('pair_port', '80'),
               ('connect_port', 0), ('code', '12345'), ('code', '12345a'), ('code', 123456)]
        for k, v in bad:
            with self.assertRaises(HttpError, msg=f'{k}={v!r}') as cm:
                devices.pair(req(body=dict(PAIR_BODY, **{k: v})))
            self.assertEqual(cm.exception.status, 400)
        d = add_device()
        for body in ({'host': '-x'}, {'connect_port': 65536}, {'connect_port': '80'}):
            with self.assertRaises(HttpError):
                devices.connect(req(params={'id': d}, body=body))
        self.assertEqual(self.fa.calls(), [])

    def test_reconnect_steps_order(self):
        d = add_device(host='192.168.1.20', port=37011, mdns_name='adb-ABC123-x1y2')
        self.browse.return_value = {'adb-ABC123-x1y2': {'service': 'connect', 'host': '192.168.1.21', 'port': 40123}}
        self.fa.rule('devices', out='List of devices attached\n', times=2)
        self.fa.rule('connect', '192.168.1.20:37011', out='failed to connect to 192.168.1.20:37011\n')
        self.fa.rule('connect', '192.168.1.21:40123', out='connected to 192.168.1.21:40123\n')
        self.fa.rule('devices', out='List of devices attached\n192.168.1.21:40123\tdevice\n')
        self.assertEqual(devices.reconnect(devices.get(d)), 'online')
        self.assertEqual(self.fa.calls(), [['devices'], ['connect', '192.168.1.20:37011'], ['devices'],
                                           ['connect', '192.168.1.21:40123'], ['devices']])
        row = devices.get(d)
        self.assertEqual((row['host'], row['connect_port']), ('192.168.1.21', 40123))
        self.assertIsNotNone(row['last_seen_at'])

    def test_unauthorized_state(self):
        d = add_device()
        self.fa.rule('devices', out='List of devices attached\n192.168.1.20:37011\tunauthorized\n')
        self.assertEqual(devices.reconnect(devices.get(d)), 'unauthorized')
        with self.assertRaises(HttpError) as cm:
            devices.connect(req(params={'id': d}))
        self.assertEqual((cm.exception.status, cm.exception.message), (409, 'UNAUTHORIZED'))

    def test_connect_offline_409(self):
        d = add_device()
        with self.assertRaises(HttpError) as cm:
            devices.connect(req(params={'id': d}, body={'connect_port': 40000}))
        self.assertEqual((cm.exception.status, cm.exception.message), (409, 'DEVICE_OFFLINE'))
        self.assertEqual(devices.get(d)['connect_port'], 40000)

    def test_patch_rejects_unknown_key(self):
        d = add_device()
        for body in ({'host': '1.2.3.4'}, {'name': ''}, {'ota_after_build': 1}, {}):
            with self.assertRaises(HttpError):
                devices.patch_device(req(params={'id': d}, body=body))
        dev = devices.patch_device(req(params={'id': d}, body={'name': ' Phone ', 'ota_after_build': True}))[1]['device']
        self.assertEqual((dev['name'], dev['ota_after_build']), ('Phone', True))

    def test_connect_rejects_app_device(self):
        d = add_app_device()
        with self.assertRaises(HttpError) as cm:
            devices.connect(req(params={'id': d}))
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("kind 'app'", cm.exception.message)
        self.assertEqual(self.fa.calls(), [])

    def test_delete_disconnects_and_drops_installs(self):
        add_catalog_item(1, 'com.google.android.youtube')
        d = add_device()
        assign(d, 1)
        jid = add_job(target={'device_id': d})
        with db.tx() as c:
            c.execute("INSERT INTO ota_installs(job_id,device_id,kind,ref_id,package,result) "
                      "VALUES (?,?,'item',1,'com.google.android.youtube','installed')", (jid, d))
        self.assertEqual(devices.delete_device(req(params={'id': d})), (200, {'deleted': d}))
        self.assertEqual(self.fa.calls(), [['disconnect', '192.168.1.20:37011']])
        self.assertIsNone(devices.get(d))
        self.assertEqual(db.conn().execute('SELECT COUNT(*) FROM ota_installs').fetchone()[0], 0)
        with self.assertRaises(HttpError) as cm:
            devices.delete_device(req(params={'id': d}))
        self.assertEqual(cm.exception.status, 404)

    def test_assignments_unknown_item_400(self):
        add_catalog_item(1, 'com.google.android.youtube')
        add_catalog_item(3, 'com.reddit.frontpage')
        d = add_device()
        with self.assertRaises(HttpError):
            devices.put_assignments(req(params={'id': d}, body={'item_ids': [99]}))
        st, body = devices.put_assignments(req(params={'id': d}, body={'item_ids': [1, 3]}))
        self.assertEqual((st, body['device']['id']), (200, d))
        self.assertEqual([r[0] for r in db.conn().execute('SELECT item_id FROM device_assignments ORDER BY item_id')], [1, 3])

    def rows(self, table, d):
        col = 'item_id' if table == 'device_assignments' else 'project_id'
        return [r[0] for r in db.conn().execute(f'SELECT {col} FROM {table} WHERE device_id=? ORDER BY {col}', (d,))]

    def test_assignments_with_project_ids(self):
        add_catalog_item(1, 'com.google.android.youtube')
        add_project(1, 'app.revanced.android.gms')
        add_project(2, 'org.example.reader')
        d = add_device()
        st, body = devices.put_assignments(req(params={'id': d}, body={'item_ids': [1], 'project_ids': [2, 1, 2]}))
        self.assertEqual((st, body['device']['id']), (200, d))
        self.assertEqual((self.rows('device_assignments', d), self.rows('device_projects', d)), ([1], [1, 2]))
        devices.put_assignments(req(params={'id': d}, body={'item_ids': [], 'project_ids': [2]}))
        self.assertEqual((self.rows('device_assignments', d), self.rows('device_projects', d)), ([], [2]))
        for bad in ('x', [1, None], [True]):
            with self.assertRaises(HttpError) as cm:
                devices.put_assignments(req(params={'id': d}, body={'item_ids': [], 'project_ids': bad}))
            self.assertEqual((cm.exception.status, cm.exception.message), (400, 'project_ids must be a list of integers'))
        self.assertEqual(self.rows('device_projects', d), [2])

    def test_assignments_unknown_project_400(self):
        add_catalog_item(1, 'com.google.android.youtube')
        d = add_device()
        with self.assertRaises(HttpError) as cm:
            devices.put_assignments(req(params={'id': d}, body={'item_ids': [1], 'project_ids': [7]}))
        self.assertEqual((cm.exception.status, cm.exception.message), (400, 'unknown project 7'))
        self.assertEqual(self.rows('device_assignments', d), [])

    def test_assignments_package_conflict_400(self):
        add_catalog_item(1, 'com.google.android.youtube')
        add_project(1, 'com.google.android.youtube')
        d = add_device()
        assign(d, 1)
        with self.assertRaises(HttpError) as cm:
            devices.put_assignments(req(params={'id': d}, body={'item_ids': [1], 'project_ids': [1]}))
        self.assertEqual((cm.exception.status, cm.exception.message),
                         (400, 'two apps with package com.google.android.youtube'))
        # omitted project_ids: the device's current projects still take part in the check
        devices.put_assignments(req(params={'id': d}, body={'item_ids': [], 'project_ids': [1]}))
        with self.assertRaises(HttpError):
            devices.put_assignments(req(params={'id': d}, body={'item_ids': [1]}))
        self.assertEqual((self.rows('device_assignments', d), self.rows('device_projects', d)), ([], [1]))

    def test_assignments_without_project_ids_keeps_rows(self):
        add_catalog_item(1, 'com.google.android.youtube')
        add_catalog_item(2, 'com.reddit.frontpage')
        add_project(1, 'app.revanced.android.gms')
        d = add_device()
        assign_projects(d, 1)
        devices.put_assignments(req(params={'id': d}, body={'item_ids': [1, 2]}))
        self.assertEqual((self.rows('device_assignments', d), self.rows('device_projects', d)), ([1, 2], [1]))

    def test_resolve_devices(self):
        a, b = add_device(serial='A'), add_device(serial='B')
        self.assertEqual(devices.resolve_devices({'all': True}), [a, b])
        self.assertEqual(devices.resolve_devices({'device_ids': [b, 999]}), [b])
        self.assertEqual(devices.resolve_devices({'item_ids': [1]}), [])

    def test_device_list_hides_token_hash(self):
        add_device()
        db.conn().execute("INSERT INTO devices(name, kind, token_hash) VALUES ('Phone', 'app', 'secret-hash')")
        self.fa.rule('devices', out='List of devices attached\n')
        from webui import ota
        rows = ota.list_devices(req())[1]['devices']
        self.assertEqual(len(rows), 2)
        self.assertFalse(any('token_hash' in r for r in rows))
        self.assertEqual([r['has_token'] for r in rows], [False, True])

    def test_discovered_route(self):
        add_device(mdns_name='adb-ABC123-x1y2')
        found = dict(DISC, **{'adb-ABC123-p': {'service': 'pairing', 'host': '192.168.1.20', 'port': 41000}})
        self.browse.side_effect = lambda: setattr(devices, 'DISCOVERED', {k: dict(v, last_seen=1.0) for k, v in found.items()})
        st, body = devices.discovered(req(method='GET'))
        self.assertEqual(body['pairing'], [{'name': 'adb-ABC123-p', 'host': '192.168.1.20', 'port': 41000, 'last_seen': 1.0}])
        self.assertEqual(body['connect'][0]['device_id'], 1)
