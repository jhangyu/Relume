import io
import sys
from unittest import mock

from webui import build, db, devices, ota, projects, settings
from webui.tests import helpers
from webui.tests.device_helpers import (FIXTURES, FakeAdb, FakeLog, add_catalog_item, add_device, add_job, assign, job,
                                        req)

FX = FIXTURES / 'appmanage'
YT, GMS, PLAIN = 'com.google.android.youtube', 'app.revanced.android.gms', 'org.example.plain'


def fx(name):
    return (FX / name).read_text()


class ParserTest(helpers.DataTestCase):
    def test_user_packages(self):
        self.assertEqual(devices.parse_user_packages(fx('pm-list-3.txt')),
                         {YT: 1547053504, GMS: 255070218, PLAIN: 7})

    def test_user_packages_bad_shape(self):
        with self.assertRaisesRegex(ValueError, 'got .*package:x'):
            devices.parse_user_packages('package:x\n')

    def test_version_names(self):
        self.assertEqual(devices.parse_version_names(fx('dumpsys-package.txt')),
                         {YT: '19.47.53', GMS: '0.3.1.4.250932'})   # PLAIN has no versionName line

    def test_diskstats(self):
        self.assertEqual(devices.parse_diskstats(fx('diskstats.txt')),
                         {YT: (400000000, 120000000, 80000000), GMS: (25000000, 3000000, 0)})

    def test_diskstats_bad_shapes(self):
        for text, expect in ((fx('diskstats-bad-lengths.txt'), 'equal array lengths'),
                             (fx('diskstats-bad-json.txt'), 'JSON array'),
                             ('System Size: 1\n', 'Package Names')):
            with self.subTest(expect=expect), self.assertRaisesRegex(ValueError, expect):
                devices.parse_diskstats(text)


class InventoryTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.fa = FakeAdb(self)
        self.d = add_device()
        self.dev = devices.get(self.d)
        self.fa.rule('list', out=fx('pm-list-3.txt')).rule('dumpsys', 'package', out=fx('dumpsys-package.txt'))

    def test_full_inventory(self):
        self.fa.rule('diskstats', out=fx('diskstats.txt'))
        apps = devices.app_inventory(self.dev)
        self.assertEqual(apps[YT], {'label': None, 'version_code': 1547053504, 'version_name': '19.47.53',
                                    'app_bytes': 400000000, 'data_bytes': 120000000, 'cache_bytes': 80000000,
                                    'size_reason': None, 'icon_sha256': None})
        self.assertIsNone(apps[PLAIN]['version_name'])
        self.assertEqual(apps[PLAIN]['size_reason'], 'dumpsys diskstats: package not listed')
        self.assertIsNone(apps[PLAIN]['app_bytes'])

    def test_missing_diskstats_nulls_all_sizes_with_reason(self):
        self.fa.rule('diskstats', out='Permission denied\n', rc=1)
        for entry in devices.app_inventory(self.dev).values():
            self.assertEqual((entry['app_bytes'], entry['data_bytes'], entry['cache_bytes']), (None,) * 3)
            self.assertEqual(entry['size_reason'], 'dumpsys diskstats: exit 1: Permission denied')

    def test_unparsable_diskstats_gives_reason(self):
        self.fa.rule('diskstats', out=fx('diskstats-bad-lengths.txt'))
        self.assertIn('equal array lengths', devices.app_inventory(self.dev)[YT]['size_reason'])

    def test_failed_pm_list_raises(self):
        fa = FakeAdb(self).rule('list', out='error: closed\n', rc=1)
        with self.assertRaisesRegex(devices.InventoryFailed, 'pm list packages -3.*got 1'):
            devices.app_inventory(self.dev)
        self.assertTrue(fa.calls())

    def test_delete_device_clears_primary(self):
        other = add_device(serial='SER2', host='192.168.1.21')
        settings.put(devices.PRIMARY_KEY, self.d)
        self.fa.rule('disconnect')
        devices.delete_device(req({'id': other}))
        self.assertEqual(settings.get(devices.PRIMARY_KEY), self.d)
        devices.delete_device(req({'id': self.d}))
        self.assertIsNone(settings.get(devices.PRIMARY_KEY))


class AdbPipeTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        old = devices.ADB
        devices.ADB = [sys.executable, str(FX / 'pipe-adb')]
        self.addCleanup(setattr, devices, 'ADB', old)

    def test_streams_every_byte_to_stdin(self):
        data = b'x' * (3 * devices.FEED_CHUNK + 5)
        rc, out = devices.adb_pipe(['shell', 'pm', 'install-write'], io.BytesIO(data), serial='S1', timeout=30)
        self.assertEqual((rc, out), (0, f'argv=-s S1 shell pm install-write stdin={len(data)}\n'))

    def test_source_read_error_is_raised(self):
        class Broken:
            def read(self, n):
                raise OSError('connection reset')
        with self.assertRaisesRegex(OSError, 'connection reset'):
            devices.adb_pipe(['shell'], Broken(), serial='S1', timeout=30)


class InstallRefTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.fa = FakeAdb(self)
        self.apk = db.DATA / 'yt.apk'
        self.apk.write_bytes(b'apk')
        add_catalog_item(1, YT)
        self.d = add_device()
        assign(self.d, 1)
        art = {'id': 60, 'job_id': 6, 'item_id': 1, 'variant': 'arm64-v8a', 'abi': 'arm64-v8a', 'package': YT,
               'version_name': '19.47.53', 'version_code': 1, 'patches_version': 'v1', 'signer_sha256': 'aa',
               'path': str(self.apk), 'size': 3, 'sha256': 'bb', 'created_at': '2026-10-19T04:00:00Z'}
        for mod, name, fn in ((build, 'latest_artifact', lambda i, abi: art),
                              (projects, 'item_projects', lambda i: []),
                              (projects, 'assigned_projects', lambda d: [])):
            self.enterContext(mock.patch.object(mod, name, side_effect=fn))
        self.fa.rule('pm', out='package:/system/priv-app/Settings/Settings.apk=com.android.settings versionCode:35\n')
        self.jid = add_job(target={'device_id': self.d})

    def rows(self):
        return [tuple(r) for r in db.conn().execute('SELECT job_id, kind, ref_id, result, error_code FROM ota_installs')]

    def test_installs_one_item(self):
        self.fa.rule('install', out='Success\n')
        res = ota.install_ref(job(self.jid), devices.get(self.d), 'item', 1, FakeLog())
        self.assertEqual(res, (None, 'installed'))
        self.assertEqual(self.rows(), [(self.jid, 'item', 1, 'installed', None)])
        self.assertIn(['install', '-r', str(self.apk)], [c[-3:] for c in self.fa.calls()])

    def test_install_failure_returns_the_code(self):
        self.fa.rule('install', out='Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]\n', rc=1)
        code, text = ota.install_ref(job(self.jid), devices.get(self.d), 'item', 1, FakeLog())
        self.assertEqual(code, 'SIGNATURE_MISMATCH')
        self.assertIn('INSTALL_FAILED_UPDATE_INCOMPATIBLE', text)

    def test_unassigned_item_has_no_step(self):
        code, text = ota.install_ref(job(self.jid), devices.get(self.d), 'item', 99, FakeLog())
        self.assertEqual(code, 'ADB_ERROR')
        self.assertIn('item 99', text)
        self.assertEqual(self.rows(), [])
