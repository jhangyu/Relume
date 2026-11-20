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
    DEV = {"name": "Pixel", "abi_list": "arm64-v8a"}

    def fake_apkeep(self, answers):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = pathlib.Path(tmp.name) / "fake-apkeep"
        script.write_text(f"#!{sys.executable}\nimport sys\nanswers = iter({list(answers)!r})\n"
                          "for line in sys.stdin:\n    print(next(answers), flush=True)\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.enterContext(mock.patch.object(build, "APKEEP", str(script)))

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
