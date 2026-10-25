#!/usr/bin/env python3
"""Run the CI steps that android.yml runs before publishing: test guard, then the release build (signed with src/keystore/lspatch-signing.p12).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# version values are what the tag step of android.yml derives; any valid pair builds the same code
VERSION_ENV = {"RELUME_COMPANION_VERSION_NAME": "0.0.0", "RELUME_COMPANION_VERSION_CODE": "1"}


def fail(step, detail):
    raise SystemExit(f"ci_local: step '{step}' failed: {detail}")


def check_sdk():
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if home and Path(home).is_dir():
        return
    props = ROOT / "android" / "local.properties"
    if props.is_file() and any(line.startswith("sdk.dir=") for line in props.read_text().splitlines()):
        return
    fail("android sdk", f"expected ANDROID_HOME/ANDROID_SDK_ROOT to be an existing directory or sdk.dir in {props}, "
         f"got ANDROID_HOME={os.environ.get('ANDROID_HOME')!r} ANDROID_SDK_ROOT={os.environ.get('ANDROID_SDK_ROOT')!r}")


def run(step, cmd, env):
    rc = subprocess.run(cmd, cwd=ROOT, env=env).returncode
    if rc != 0:
        fail(step, f"{' '.join(cmd)} exited {rc}")


def main():
    env = {**VERSION_ENV, **os.environ}
    run("ci_guard", [sys.executable, "scripts/ci_guard.py"], env)
    check_sdk()
    run("assembleRelease", ["android/gradlew", "-p", "android", "assembleRelease", "--no-watch-fs"], env)
    print("ci_local: all CI steps passed")


if __name__ == "__main__":
    main()
