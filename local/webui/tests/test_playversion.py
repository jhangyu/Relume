import pathlib
import stat
import sys
import tempfile
import traceback
import unittest
from unittest import mock

from webui import apkmanifest, playversion, upstream
from webui.playversion import MAX_PLAY_CALLS, EXODUS_SEARCH, APKPURE_API, PlayClient, VersionCodeError
from webui.tests import helpers
from webui.tests.catalog_rows import FakeHTTP, FakeLog

FIX = pathlib.Path(__file__).with_name("fixtures") / "playversion"
PICSART, JPTT = "com.picsart.studio", "com.joshua.jptt"
# jptt on Play: APKPure list plus probe facts (code 290 does not exist)
JPTT_PAIRS = {250: "3.1.2", 254: "3.1.6", 256: "3.2", 264: "3.3.4", 265: "3.3.5", 266: "3.3.6", 267: "3.4",
              270: "3.4.3", 271: "3.4.4", 273: "3.5.1", 274: "3.5.2", 275: "3.5.3", 276: "3.5.3", 277: "3.5.4",
              278: "3.5.5", 280: "3.5.7", 283: "3.5.10", 284: "3.5.11", 289: "3.5.16", 291: "3.5.17", 292: "3.6",
              294: "3.7.1", 295: "3.7.2", 296: "3.7.3", 297: "3.8", 298: "3.8.1", 300: "3.8.3", 301: "3.8.4",
              303: "3.8.5", 306: "3.9.1", 307: "3.9.2", 309: "3.9.4"}


def fixture(name):
    return (FIX / name).read_bytes()


MANIFEST_APK = FIX.parent / "apkmanifest" / "manifest-only.apk"


class FakePlay:
    """PlayClient interface over a {code: versionName} oracle; latest = (name, code)."""
    device = "px_9a"

    def __init__(self, package, pairs, latest, fail_at=None):
        self.package_name, self.pairs, self.latest, self.fail_at = package, pairs, latest, fail_at
        self.calls = 0
        self.package = None
        self.probed = []
        self.closed = 0

    def details(self):
        self.calls += 1
        return self.latest

    def version_at(self, code):
        self.calls += 1
        self.probed.append(code)
        if code == self.fail_at:
            raise VersionCodeError(f"Play probe {code} failed: network down")
        name = self.pairs.get(code)
        return None if name is None else apkmanifest.Manifest(self.package_name, code, name)

    def close(self):
        self.closed += 1


class ResolveCase(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        self.log = FakeLog()

    def web(self, routes=None):
        http = FakeHTTP(routes or {})
        self.enterContext(mock.patch.object(upstream, "http_get", http))
        return http

    def resolve(self, package, name, play):
        return playversion.resolve(package, name, self.log, play)

    def test_pattern_from_latest_pair(self):
        play = FakePlay(PICSART, {993831007: "31.0.7", 993830708: "30.7.8"}, ("31.0.7", 993831007))
        http = self.web()
        self.assertEqual(self.resolve(PICSART, "30.7.8", play), 993830708)
        self.assertEqual((play.calls, play.closed, http.calls), (3, 1, []))

    def test_bisection_on_dense_codes(self):
        play = FakePlay(JPTT, JPTT_PAIRS, ("3.9.4", 309))
        http = self.web()
        self.assertEqual(self.resolve(JPTT, "3.5.16", play), 289)
        self.assertLessEqual(play.calls, MAX_PLAY_CALLS)
        self.assertEqual(http.calls, [])

    def test_web_hint_when_pattern_and_bisection_fail(self):
        play = FakePlay(PICSART, {993831017: "31.0.7", 993830708: "30.7.8"}, ("31.0.7", 993831017))
        http = self.web({EXODUS_SEARCH.format(PICSART): (200, fixture("exodus-search-picsart.html"), {}),
                         "https://reports.exodus-privacy.eu.org/en/reports/781184/":
                             (200, fixture("exodus-report-781184.html"), {})})
        self.assertEqual(self.resolve(PICSART, "30.7.8", play), 993830708)
        self.assertEqual(play.probed[-1], 993830708)
        self.assertEqual(len(http.calls), 2)

    def test_unknown_version_names_steps_and_count(self):
        play = FakePlay(PICSART, {993831007: "31.0.7"}, ("31.0.7", 993831007))
        search = fixture("exodus-search-picsart.html").replace(b'href="?page=2">Next', b'href="#">Next')
        self.web({EXODUS_SEARCH.format(PICSART): (200, search, {}),
                  APKPURE_API.format(PICSART): (200, fixture("apkpure-picsart.bin"), {})})
        with self.assertRaises(VersionCodeError) as cm:
            self.resolve(PICSART, "30.7.999", play)
        text = str(cm.exception)
        for part in (PICSART, "30.7.999", "pattern", "bisection", "Exodus Privacy", "APKPure",
                     f"Play calls {play.calls} of {MAX_PLAY_CALLS}"):
            self.assertIn(part, text)
        self.assertEqual(play.closed, 1)

    def test_probe_error_aborts(self):
        play = FakePlay(PICSART, {993831007: "31.0.7"}, ("31.0.7", 993831007), fail_at=993830708)
        http = self.web()
        with self.assertRaises(VersionCodeError) as cm:
            self.resolve(PICSART, "30.7.8", play)
        self.assertIn("network down", str(cm.exception))
        self.assertIn("details: latest 31.0.7", str(cm.exception))
        self.assertEqual((http.calls, play.closed), ([], 1))

    def test_anchor_not_found_names_device(self):
        play = FakePlay(PICSART, {}, ("31.0.7", 993831007))
        http = self.web()
        with self.assertRaises(VersionCodeError) as cm:
            self.resolve(PICSART, "30.7.8", play)
        self.assertIn("device profile px_9a cannot get com.picsart.studio", str(cm.exception))
        self.assertEqual(http.calls, [])

    def test_cache_hit_skips_play(self):
        playversion.remember(PICSART, "30.7.8", "px_9a", 993830708)
        play = FakePlay(PICSART, {}, ("31.0.7", 993831007))
        http = self.web()
        self.assertEqual(self.resolve(PICSART, "30.7.8", play), 993830708)
        self.assertEqual((play.calls, http.calls), (0, []))

    def test_cache_is_per_device(self):
        playversion.remember(PICSART, "30.7.8", "other_device", 1)
        play = FakePlay(PICSART, {993831007: "31.0.7", 993830708: "30.7.8"}, ("31.0.7", 993831007))
        self.web()
        self.assertEqual(self.resolve(PICSART, "30.7.8", play), 993830708)


class ApkpureCase(unittest.TestCase):
    def test_codes_from_trimmed_response(self):
        body = fixture("apkpure-jptt.bin")
        self.assertEqual(playversion.apkpure_codes(body, JPTT, "3.9.2"), {307})
        self.assertEqual(playversion.apkpure_codes(body, JPTT, "9.9.9"), set())


def range_get(blob, calls):
    """upstream.http_get stand-in serving blob with Range support behind a redirect to a CDN URL."""
    def get(url, headers=None, timeout=30, max_bytes=None):
        calls.append(url)
        a, b = headers["Range"].removeprefix("bytes=").split("-")
        a, b = int(a), min(int(b), len(blob) - 1)
        return 206, {"content-range": f"bytes {a}-{b}/{len(blob)}"}, blob[a:b + 1], "https://cdn.example/apk"
    return get


class ManifestCase(unittest.TestCase):
    def test_manifest_through_range_reads(self):
        calls = []
        with mock.patch.object(upstream, "http_get", range_get(MANIFEST_APK.read_bytes(), calls)):
            m = PlayClient._read_manifest("https://play.example/dl?token=secret")
        self.assertEqual(m, apkmanifest.Manifest("com.rawcam.app", 57, "1.4.5"))
        self.assertEqual(calls[0], "https://play.example/dl?token=secret")
        self.assertEqual(set(calls[1:]), {"https://cdn.example/apk"})

    def test_truncated_apk_is_an_error(self):
        blob = MANIFEST_APK.read_bytes()
        with mock.patch.object(upstream, "http_get", range_get(blob[:-40], [])):
            with self.assertRaises(VersionCodeError):
                PlayClient._read_manifest("https://play.example/dl")

    def test_non_206_is_an_error(self):
        with mock.patch.object(upstream, "http_get", return_value=(200, {}, b"x", "u")):
            with self.assertRaisesRegex(VersionCodeError, "expected status 206, got 200"):
                PlayClient._read_manifest("https://play.example/dl")

    def test_oversize_body_error_hides_signed_url(self):
        def over(url, headers=None, timeout=30, max_bytes=None):
            raise ValueError(f"{url}: body over {max_bytes} bytes")
        with mock.patch.object(upstream, "http_get", over):
            with self.assertRaises(VersionCodeError) as cm:
                PlayClient._read_manifest("https://play.example/dl?token=SECRET")
        text = "".join(traceback.format_exception(cm.exception))
        self.assertNotIn("SECRET", text)
        self.assertIn("play.example", str(cm.exception))
        self.assertIn("at most 1 bytes", str(cm.exception))


class SessionCase(helpers.DataTestCase):
    """PlayClient against a python3 script that stands in for apkeep (canned answers, one per stdin line)."""

    def client(self, answers=(), *, exit_code=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = pathlib.Path(tmp.name) / "fake-apkeep"
        body = (f"print('login blocked')\nsys.stderr.write('bad token')\nsys.exit({exit_code})\n" if exit_code else
                f"print('Terms of Service notice')\nanswers = iter({list(answers)!r})\n"
                "for line in sys.stdin:\n    print(next(answers), flush=True)\n")
        script.write_text(f"#!{sys.executable}\nimport sys\n" + body)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        c = PlayClient(str(script), pathlib.Path(tmp.name) / "x.ini", "px_9a", FakeLog())
        c.package = PICSART
        self.addCleanup(c.close)
        return c

    def test_details_skips_non_json_lines(self):
        c = self.client(['{"pkg":"p","mode":"details","ok":true,"versionCode":7,"versionString":"1.0"}'])
        self.assertEqual(c.details(), ("1.0", 7))
        proc = c._proc
        c.close()
        self.assertEqual(proc.poll(), 0)

    def test_delivery_found_and_not_found(self):
        c = self.client(['{"mode":"delivery","vc":5,"ok":true,"url":"https://dl.example/a"}',
                         '{"mode":"delivery","vc":6,"ok":false,"notFound":true,"error":"x"}'])
        manifest = apkmanifest.Manifest(PICSART, 5, "1.0")
        with mock.patch.object(PlayClient, "_read_manifest", return_value=manifest) as read:
            self.assertEqual(c.version_at(5), manifest)
        read.assert_called_once_with("https://dl.example/a")
        self.assertIsNone(c.version_at(6))
        self.assertEqual(c.calls, 2)

    def test_other_failure_raises(self):
        c = self.client(['{"mode":"delivery","vc":5,"ok":false,"notFound":false,"error":"HTTP 500"}'])
        with self.assertRaisesRegex(VersionCodeError, "HTTP 500"):
            c.version_at(5)

    def test_answer_for_another_code_raises(self):
        c = self.client(['{"mode":"delivery","vc":6,"ok":false,"notFound":true,"error":"x"}'])
        with self.assertRaisesRegex(VersionCodeError, "expected delivery/5"):
            c.version_at(5)

    def test_exit_without_answer_raises_with_exit_code_and_stderr(self):
        c = self.client(exit_code=3)
        with self.assertRaisesRegex(VersionCodeError, "exit 3.*bad token"):
            c.details()

    def test_probe_dead_before_first_request_raises_version_code_error(self):
        c = self.client(exit_code=3)
        c._start()
        c._proc.wait()  # the pipe is already broken when the request is written
        with self.assertRaisesRegex(VersionCodeError, "exit 3.*bad token"):
            c.details()
        c.close()  # must not raise BrokenPipeError over the lookup error


if __name__ == "__main__":
    unittest.main()
