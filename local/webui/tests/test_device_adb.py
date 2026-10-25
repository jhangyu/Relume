import threading
from unittest import mock

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
        got = devices.parse_packages((FIXTURES / 'pm-list.txt').read_text())
        self.assertEqual({k: v['version_code'] for k, v in got.items()},
                         {'com.google.android.youtube': 1547053504, 'app.revanced.android.gms': 255070218,
                          'com.android.settings': 35})
        self.assertEqual(got['com.android.settings'], {'version_code': 35, 'version_name': None, 'signer_sha256': None})
        with self.assertRaisesRegex(ValueError, "got 'package:a.b'"):
            devices.parse_packages('package:a.b\n')

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
        fa.rule('install', hang=True)   # hangs 60 s: only the kill can end it within the test budget
        with self.assertRaises(devices.AdbTimeout):
            devices.adb(['install', '-r', 'x.apk'], timeout=0)

    def test_adb_cancel_kills(self):
        fa = FakeAdb(self)
        fa.rule('install', hang=True)
        self.addCleanup(setattr, devices, 'CANCEL_CHECK_S', devices.CANCEL_CHECK_S)
        devices.CANCEL_CHECK_S = 0      # look at the cancel flag at once instead of once a second
        ev = threading.Event()
        ev.set()
        with self.assertRaises(devices.AdbTimeout):
            devices.adb(['install', 'x.apk'], timeout=60, cancel_event=ev)

    def test_adb_log_lines(self):
        fa = FakeAdb(self)
        fa.rule('install', out='Performing Streamed Install\nSuccess\n')
        log = FakeLog()
        devices.adb(['install', 'x.apk'], log=log)
        self.assertEqual(log.lines, ['adb: Performing Streamed Install', 'adb: Success'])

    def test_adb_serialized_per_device(self):
        held = []

        def fake_exec(argv, timeout, cancel_event):
            held.append((argv[-1], devices._lock('1.2.3.4:5555').locked(), devices._lock('5.6.7.8:5555').locked()))
            return 0, ''
        with mock.patch.object(devices, '_exec', side_effect=fake_exec):
            devices.adb(['shell', 'a'], serial='1.2.3.4:5555')
            devices.adb(['shell', 'b'], serial='5.6.7.8:5555')
        # each adb run holds exactly its own serial's lock: same serial waits, other serials run in parallel
        self.assertEqual(held, [('a', True, False), ('b', False, True)])
        self.assertIs(devices._lock('1.2.3.4:5555'), devices._lock('1.2.3.4:5555'))

    def test_adb_missing_binary(self):
        old = devices.ADB
        devices.ADB = ['/nonexistent/adb']
        self.addCleanup(setattr, devices, 'ADB', old)
        self.assertEqual(devices.adb(['devices']), (127, 'adb not found'))
