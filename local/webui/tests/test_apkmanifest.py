import pathlib, unittest
from unittest import mock
from webui import apkmanifest

APK = (pathlib.Path(__file__).with_name('fixtures') / 'apkmanifest' / 'manifest-only.apk').read_bytes()


def reader(data):
    return lambda off, n: data[off:off + n]


class ReadManifestTest(unittest.TestCase):
    def test_fixture_matches_aapt2(self):
        m = apkmanifest.read_manifest(reader(APK), len(APK))
        self.assertEqual(m, ('com.rawcam.app', 57, '1.4.5'))

    def test_truncated_and_garbage_raise(self):
        cut = APK[:len(APK) - 30]
        for data in (cut, b'x' * 500, b''):
            with self.assertRaises(apkmanifest.ManifestError):
                apkmanifest.read_manifest(reader(data), len(data))

    def test_short_read_raises(self):
        with self.assertRaises(apkmanifest.ManifestError):
            apkmanifest.read_manifest(lambda off, n: b'', len(APK))

    def test_manifest_byte_cap(self):
        with mock.patch.object(apkmanifest, 'MAX_MANIFEST_BYTES', 100):
            with self.assertRaisesRegex(apkmanifest.ManifestError, 'manifest size'):
                apkmanifest.read_manifest(reader(APK), len(APK))
        with mock.patch.object(apkmanifest, 'MAX_CENTRAL_DIR', 10):
            with self.assertRaisesRegex(apkmanifest.ManifestError, 'central directory size'):
                apkmanifest.read_manifest(reader(APK), len(APK))
