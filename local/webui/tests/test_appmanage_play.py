import json
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

from webui import appmanage_play, build, db, settings
from webui.appmanage_play import PlayLookupError
from webui.tests import helpers
from webui.tests.catalog_rows import FakeLog
from webui.tests.device_helpers import FakeAdb

DETAILS = '{"mode":"details","ok":true,"versionString":"9.2","versionCode":77}'
DELIVERY = ('{"mode":"delivery","vc":77,"ok":true,"url":"https://play.example/base?token=SECRET&cpn=x",'
            '"splits":[{"name":"config.arm64_v8a","url":"https://play.example/a?token=SECRET2"},'
            '{"name":"config.en","url":"https://play.example/e?token=SECRET3"}]}')


class ProfileCase(unittest.TestCase):
    def test_each_abi_and_first_abi_rule(self):
        for abi, profile in build.APKEEP_DEVICE.items():
            self.assertEqual(appmanage_play.profile_for({"name": "p", "abi_list": abi}), profile)
        self.assertEqual(appmanage_play.profile_for({"name": "p", "abi_list": "arm64-v8a,armeabi-v7a"}), "px_9a")

    def test_unknown_or_missing_abi(self):
        for abis in ("mips", ""):
            with self.assertRaises(PlayLookupError) as cm:
                appmanage_play.profile_for({"name": "p", "abi_list": abis})
            self.assertEqual(cm.exception.code, "NO_PLAY_PROFILE")
            self.assertIn("device p", str(cm.exception))


class ResolveCase(helpers.DataTestCase):
    DEV = {"name": "Pixel", "abi_list": "arm64-v8a", "kind": "app", "locale": "zh-Hant-TW"}

    def fake_apkeep(self, answers):
        """Answers one stdin line each; returns the path its argv is written to."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = pathlib.Path(tmp.name) / "fake-apkeep"
        argv = pathlib.Path(tmp.name) / "argv.json"
        script.write_text(f"#!{sys.executable}\nimport json, sys\nanswers = iter({list(answers)!r})\n"
                          f"open({str(argv)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
                          "for line in sys.stdin:\n    print(next(answers), flush=True)\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.enterContext(mock.patch.object(build, "APKEEP", str(script)))
        return argv

    def apkeep_options(self, argv):
        args = json.loads(argv.read_text())
        return args[args.index("-o") + 1].split(",")

    def creds(self):
        settings.write_secret(settings.google_play_path(), json.dumps({"email": "a@b.c", "aas_token": "t"}))

    def inis(self):
        return list(db.KEY.glob("apkeep-am-*.ini"))

    def test_base_first_and_ini_removed(self):
        self.creds()
        self.fake_apkeep([DETAILS, DELIVERY])
        log = FakeLog()
        r = appmanage_play.resolve("com.x", self.DEV, log)
        self.assertEqual([a["name"] for a in r["apks"]], ["base", "config.arm64_v8a", "config.en"])
        self.assertEqual(r["version_code"], 77)
        self.assertEqual(self.inis(), [])
        self.assertNotIn("token=", "\n".join(log.lines))
        self.assertNotIn("SECRET", "\n".join(log.lines))

    def test_failure_no_leak_and_ini_removed(self):
        self.creds()
        self.fake_apkeep([DETAILS, '{"mode":"delivery","vc":77,"ok":false,"error":"purchase refused"}'])
        with self.assertRaises(PlayLookupError) as cm:
            appmanage_play.resolve("com.x", self.DEV)
        self.assertEqual(cm.exception.code, "PLAY_LOOKUP_FAILED")
        self.assertIn("com.x", str(cm.exception))
        self.assertIn("Pixel", str(cm.exception))
        self.assertEqual(self.inis(), [])

    def test_no_credentials(self):
        with self.assertRaises(PlayLookupError) as cm:
            appmanage_play.resolve("com.x", self.DEV)
        self.assertEqual(cm.exception.code, "PLAY_LOOKUP_FAILED")
        self.assertIn("credentials", str(cm.exception))
        self.assertEqual(self.inis(), [])

    def test_app_row_locale_reaches_apkeep(self):
        self.creds()
        argv = self.fake_apkeep([DETAILS, DELIVERY])
        appmanage_play.resolve("com.x", self.DEV)
        self.assertIn("locale=zh_TW", self.apkeep_options(argv))

    def test_adb_row_locale_from_getprop(self):
        self.creds()
        argv = self.fake_apkeep([DETAILS, DELIVERY])
        fa = FakeAdb(self).rule("persist.sys.locale", out="\n").rule("ro.product.locale", out="zh-TW\n")
        dev = {"name": "Tab", "abi_list": "arm64-v8a", "kind": "adb", "locale": None, "host": "10.0.0.2",
               "connect_port": 5555}
        appmanage_play.resolve("com.x", dev)
        self.assertIn("locale=zh_TW", self.apkeep_options(argv))
        self.assertEqual([c[-1] for c in fa.calls()], ["persist.sys.locale", "ro.product.locale"])

    def test_missing_locale_fails_loudly(self):
        self.creds()
        self.fake_apkeep([DETAILS, DELIVERY])
        for dev, adb_out in (({**self.DEV, "locale": None}, None),
                             ({"name": "Tab", "abi_list": "arm64-v8a", "kind": "adb", "host": "10.0.0.2",
                               "connect_port": 5555}, "\n")):
            if adb_out is not None:
                FakeAdb(self).rule("getprop", out=adb_out)
            with self.assertRaises(PlayLookupError) as cm:
                appmanage_play.resolve("com.x", dev)
            self.assertEqual(cm.exception.code, "PLAY_LOOKUP_FAILED")
            self.assertIn(f"device {dev['name']}", str(cm.exception))
