import threading
import time

from webui import db, devices
from webui.tests import helpers
from webui.tests.device_helpers import FIXTURES, FakeAdb, FakeLog, add_catalog_item, add_device, add_job, assign


class ParserTest(helpers.DataTestCase):
    def test_parse_devices_states(self):
        st = devices.parse_devices((FIXTURES / 'adb-devices.txt').read_text())
        self.assertEqual(st, {'192.168.1.20:37011': 'device', '192.168.1.21:40001': 'offline',
                              '192.168.1.22:41234': 'unauthorized'})

    def test_parse_getprop_facts(self):
        p = devices.parse_getprop((FIXTURES / 'getprop.txt').read_text())
        self.assertEqual(p['ro.serialno'], '32051JEHN09137')
        self.assertEqual(p['ro.product.cpu.abilist'], 'arm64-v8a,armeabi-v7a,armeabi')
        self.assertEqual(p['ro.build.version.sdk'], '35')

    def test_parse_packages(self):
        self.assertEqual(devices.parse_packages((FIXTURES / 'pm-list.txt').read_text()),
                         {'com.google.android.youtube', 'app.revanced.android.gms', 'com.android.settings'})

    def test_install_error_codes(self):
        cases = {
            'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package x signatures do not match]': 'SIGNATURE_MISMATCH',
            'Failure [INSTALL_FAILED_VERSION_DOWNGRADE]': 'VERSION_DOWNGRADE',
            'Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]': 'INSUFFICIENT_STORAGE',
            'Failure [INSTALL_FAILED_NO_MATCHING_ABIS: ...]': 'NO_MATCHING_ABI',
            'Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]': 'USER_REJECTED',
            'Failure [INSTALL_FAILED_ABORTED: User rejected permissions]': 'USER_REJECTED',
            'Failure [INSTALL_FAILED_OTHER]': 'ADB_ERROR',
            'adb: failed to install x.apk': 'ADB_ERROR',
        }
        for text, code in cases.items():
            self.assertEqual(devices.install_error(text), code, text)
        self.assertIsNone(devices.install_error('Performing Streamed Install\nSuccess\n'))

    def test_schema_cascade(self):
        add_catalog_item(1, 'com.google.android.youtube')
        d = add_device()
        assign(d, 1)
        jid = add_job(target={'device_id': d})
        with db.tx() as c:
            c.execute("INSERT INTO ota_installs(job_id,device_id,kind,ref_id,package,result) "
                      "VALUES (?,?,'item',1,'com.google.android.youtube','installed')", (jid, d))
            c.execute('DELETE FROM devices WHERE id=?', (d,))
        c = db.conn()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM device_assignments').fetchone()[0], 0)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM ota_installs').fetchone()[0], 1)
        d2 = add_device(serial='SER2')
        assign(d2, 1)
        with db.tx() as c:
            c.execute('DELETE FROM item WHERE id=1')
        self.assertEqual(db.conn().execute('SELECT COUNT(*) FROM device_assignments').fetchone()[0], 0)


class RunnerTest(helpers.DataTestCase):
    def test_adb_argv_and_serial(self):
        fa = FakeAdb(self)
        fa.rule('getprop', out='[ro.serialno]: [X]\n')
        rc, out = devices.adb(['shell', 'getprop'], serial='1.2.3.4:5555')
        self.assertEqual((rc, out), (0, '[ro.serialno]: [X]\n'))
        self.assertEqual(fa.calls(), [['-s', '1.2.3.4:5555', 'shell', 'getprop']])

    def test_adb_daemon_retry(self):
        fa = FakeAdb(self)
        fa.rule('getprop', out='* cannot connect to daemon\n', rc=1, times=1)
        rc, out = devices.adb(['shell', 'getprop'])
        self.assertEqual(rc, 0)
        self.assertEqual(fa.calls(), [['shell', 'getprop'], ['start-server'], ['shell', 'getprop']])

    def test_adb_timeout_kills(self):
        fa = FakeAdb(self)
        fa.rule('install', hang=True)
        t = time.monotonic()
        with self.assertRaises(devices.AdbTimeout):
            devices.adb(['install', '-r', 'x.apk'], timeout=1)
        self.assertLess(time.monotonic() - t, 3)

    def test_adb_cancel_kills(self):
        fa = FakeAdb(self)
        fa.rule('install', hang=True)
        ev = threading.Event()
        threading.Timer(0.3, ev.set).start()
        with self.assertRaises(devices.AdbTimeout):
            devices.adb(['install', 'x.apk'], timeout=60, cancel_event=ev)

    def test_adb_log_lines(self):
        fa = FakeAdb(self)
        fa.rule('install', out='Performing Streamed Install\nSuccess\n')
        log = FakeLog()
        devices.adb(['install', 'x.apk'], log=log)
        self.assertEqual(log.lines, ['adb: Performing Streamed Install', 'adb: Success'])

    def test_adb_serialized_per_device(self):
        fa = FakeAdb(self)
        fa.rule('shell', delay=0.6)

        def two(serial_a, serial_b):
            ts = [threading.Thread(target=devices.adb, args=(['shell', 'x'],), kwargs={'serial': s})
                  for s in (serial_a, serial_b)]
            t = time.monotonic()
            for th in ts:
                th.start()
            for th in ts:
                th.join()
            return time.monotonic() - t
        self.assertGreaterEqual(two('1.2.3.4:5555', '1.2.3.4:5555'), 1.2)
        self.assertLess(two('1.2.3.4:5555', '5.6.7.8:5555'), 1.15)

    def test_adb_missing_binary(self):
        old = devices.ADB
        devices.ADB = ['/nonexistent/adb']
        self.addCleanup(setattr, devices, 'ADB', old)
        self.assertEqual(devices.adb(['devices']), (127, 'adb not found'))
