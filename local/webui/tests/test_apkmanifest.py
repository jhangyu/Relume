import hashlib, pathlib, unittest
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


FIXDIR = pathlib.Path(__file__).with_name('fixtures') / 'apkmanifest'
SIGNED = (FIXDIR / 'signed.apk').read_bytes()
SIGNED_V1 = (FIXDIR / 'signed-v1.apk').read_bytes()
SIGNED_DIGEST = 'd48bd2eaf4db81c9325a4ff0667cf749cdff8b0c463185125827c9a6ccac0e5e'  # apksigner verify --print-certs, Signer #1


class ReadSignerTest(unittest.TestCase):
    def test_signer_matches_apksigner(self):
        self.assertEqual(apkmanifest.read_signer(reader(SIGNED), len(SIGNED)), SIGNED_DIGEST)

    def test_manifest_of_signed_fixture(self):
        self.assertEqual(apkmanifest.read_manifest(reader(SIGNED), len(SIGNED)), ('com.rawcam.app', 57, '1.4.5'))

    def test_v1_only_has_no_signer_block(self):
        with self.assertRaisesRegex(apkmanifest.ManifestError, 'v2 or v3'):
            apkmanifest.read_signer(reader(SIGNED_V1), len(SIGNED_V1))

    def test_corrupt_signing_block_raises(self):
        cd_off, _ = apkmanifest._central_dir(reader(SIGNED), len(SIGNED))
        big = bytearray(SIGNED)
        big[cd_off - 24:cd_off - 16] = (cd_off + 1).to_bytes(8, 'little')  # block size past the file start
        cert = bytearray(SIGNED)
        der_starts = [i for i in range(len(SIGNED) - 4) if SIGNED[i:i + 2] == b'\x30\x82' and hashlib.sha256(
            SIGNED[i:i + 4 + int.from_bytes(SIGNED[i + 2:i + 4], 'big')]).hexdigest() == SIGNED_DIGEST]
        self.assertEqual(len(der_starts), 2)  # the certificate in the v2 and in the v3 signer
        for i in der_starts:
            cert[i - 4:i] = (0x7FFFFFFF).to_bytes(4, 'little')  # length prefix past the enclosing slice
        for data in (bytes(big), bytes(cert), SIGNED[:len(SIGNED) - 30]):
            with self.assertRaises(apkmanifest.ManifestError):
                apkmanifest.read_signer(reader(data), len(data))
