#!/usr/bin/env python3
"""Single test entry: python, shell and android legs under one time budget (stdlib only).

Usage: python3 scripts/test.py [--legs python,shell,android] [--root REPO_ROOT]
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

TOTAL_BUDGET_S = 90
PER_TEST_BUDGET_S = 1.0
SLOWEST_SHOWN = 10
# Each child spawns subprocesses (bash, jq, sqlite I/O): half the cores keeps wall time close to test time.
PYTHON_WORKERS = max(1, (os.cpu_count() or 2) // 2)
ALL_LEGS = ("python", "shell", "android")
FORBIDDEN = re.compile(r"sleep|delay\(", re.IGNORECASE)
CHILD_FLAG = "--child"
OUTCOME_FAIL = ("failure", "error")
OUTCOME_SKIP = "skipped"


def fmt_exc(err):
    return "".join(traceback.format_exception(*err))


class BudgetExceeded(Exception):
    pass


class ProtocolError(Exception):
    pass


_start = time.monotonic()


def run_bounded(leg, cmd, **kwargs):
    """subprocess.run with a fail-fast timeout: whatever is left of TOTAL_BUDGET_S.

    The child leads its own process group; on timeout the whole group is killed so a hung grandchild cannot outlive the budget."""
    remaining = TOTAL_BUDGET_S - (time.monotonic() - _start)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=max(remaining, 0))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise BudgetExceeded(f"{leg} leg: {' '.join(map(str, cmd[:4]))} still running after "
                                 f"{time.monotonic() - _start:.1f}s, budget {TOTAL_BUDGET_S}s") from None
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


class JsonLineResult(unittest.TestResult):
    """Prints one JSON line per finished test: {id, seconds, outcome, detail}."""

    def __init__(self):
        super().__init__()
        self._started = {}

    def startTest(self, test):
        super().startTest(test)
        self._started[test.id()] = time.monotonic()

    def _emit(self, test, outcome, detail=""):
        seconds = time.monotonic() - self._started.pop(test.id(), time.monotonic())
        print(json.dumps({"id": test.id(), "seconds": seconds, "outcome": outcome, "detail": detail}), flush=True)

    def addSuccess(self, test):
        self._emit(test, "pass")

    def addFailure(self, test, err):
        self._emit(test, "failure", fmt_exc(err))

    def addError(self, test, err):
        self._emit(test, "error", fmt_exc(err))

    def addSkip(self, test, reason):
        self._emit(test, OUTCOME_SKIP, reason)

    def addExpectedFailure(self, test, _err):
        self._emit(test, "pass")

    def addUnexpectedSuccess(self, test):
        self._emit(test, "failure", "unexpected success")

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._emit(subtest, "failure", fmt_exc(err))


def run_child(module):
    suite = unittest.defaultTestLoader.loadTestsFromName(module)
    suite.run(JsonLineResult())


def python_leg(root):
    tests_dir = root / "local" / "webui" / "tests"
    modules = sorted(p.stem for p in tests_dir.glob("test_*.py"))
    env = dict(os.environ, PYTHONPATH=str(root / "local"))
    script = Path(__file__).resolve()

    def one(module):
        proc = run_bounded("python", [sys.executable, str(script), CHILD_FLAG, f"webui.tests.{module}"],
                           cwd=root / "local", env=env)
        units = []
        for line in proc.stdout.splitlines():
            try:
                unit = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-result stdout from the module under test
            if not isinstance(unit, dict):
                raise ProtocolError(f"protocol error, python leg: webui.tests.{module} printed JSON that is not a result object: {line[:200]}")
            units.append(unit)
        if proc.returncode != 0 or not units:
            units.append({"id": f"webui.tests.{module}", "seconds": 0.0, "outcome": "error",
                          "detail": f"child rc={proc.returncode}, results={len(units)}\n{proc.stderr[-2000:]}"})
        return units

    with concurrent.futures.ThreadPoolExecutor(max_workers=PYTHON_WORKERS) as pool:
        return [u for units in pool.map(one, modules) for u in units]


CASE_LINE = re.compile(r"(PASS|FAIL) (\S+?):?(?: |$)")
SHELL_SCRIPT = "src/build/test-build-item.sh"


def shell_leg(root):
    """Each PASS/FAIL line the script prints is one test, timed from the previous case line."""
    if shutil.which("bash") is None:
        raise SystemExit("shell leg: required tool 'bash' not found on PATH")
    proc = subprocess.Popen(["bash", SHELL_SCRIPT], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    timed_out = threading.Event()

    def expire():
        timed_out.set()
        os.killpg(proc.pid, signal.SIGKILL)

    watchdog = threading.Timer(max(TOTAL_BUDGET_S - (time.monotonic() - _start), 0), expire)
    watchdog.start()
    units, other, mark = [], [], time.monotonic()
    try:
        for line in proc.stdout:
            now = time.monotonic()
            m = CASE_LINE.match(line)
            if m:
                units.append({"id": f"{SHELL_SCRIPT}:{m.group(2)}", "seconds": now - mark,
                              "outcome": "pass" if m.group(1) == "PASS" else "failure",
                              "detail": "" if m.group(1) == "PASS" else line.strip()})
                mark = now
            else:
                other.append(line)
        rc = proc.wait()
    finally:
        watchdog.cancel()
    if timed_out.is_set():
        raise BudgetExceeded(f"shell leg: {SHELL_SCRIPT} still running after {time.monotonic() - _start:.1f}s, budget {TOTAL_BUDGET_S}s")
    if not units or (rc != 0 and not any(u["outcome"] == "failure" for u in units)):
        units.append({"id": SHELL_SCRIPT, "seconds": 0.0, "outcome": "error",
                      "detail": f"rc={rc}, {len(units)} case lines\n{''.join(other)[-3000:]}"})
    return units


def android_leg(root):
    sdk = os.environ.get("ANDROID_HOME")
    if not sdk:
        raise SystemExit("android leg: environment variable ANDROID_HOME is not set")
    gradlew = root / "android" / "gradlew"
    if not gradlew.is_file():
        raise SystemExit(f"android leg: {gradlew} not found")
    results = root / "android" / "app" / "build" / "test-results" / "testDebugUnitTest"
    # stale reports must not be read as this run's results
    for old in results.glob("*.xml"):
        old.unlink()
    proc = run_bounded("android", [str(gradlew), "-p", "android", "testDebugUnitTest", "--console=plain", "--no-watch-fs"],
                       cwd=root)
    units = []
    for report in sorted(results.glob("*.xml")):
        for case in ET.parse(report).getroot().iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                outcome = "failure"
            elif case.find("skipped") is not None:
                outcome = OUTCOME_SKIP
            else:
                outcome = "pass"
            units.append({"id": f"{case.get('classname')}.{case.get('name')}",
                          "seconds": float(case.get("time", "0")), "outcome": outcome, "detail": ""})
    if proc.returncode != 0:
        units.append({"id": "gradle testDebugUnitTest", "seconds": 0.0, "outcome": "error",
                      "detail": f"rc={proc.returncode}\n{(proc.stdout + proc.stderr)[-3000:]}"})
    elif not units:
        units.append({"id": "gradle testDebugUnitTest", "seconds": 0.0, "outcome": "error",
                      "detail": f"gradle succeeded but {results} holds no test reports"})
    return units


def forbidden_words(root):
    targets = [root / "local" / "webui" / "tests", root / "src" / "build" / "test-fixtures",
               root / "android" / "app" / "src" / "test", root / "src" / "build" / "test-build-item.sh"]
    hits = []
    for target in targets:
        files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else []
        for path in files:
            if "__pycache__" in path.parts:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue  # binary fixture
            hits += [f"{path.relative_to(root)}:{n}: {line.strip()}"
                     for n, line in enumerate(lines, 1) if FORBIDDEN.search(line)]
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legs", default=",".join(ALL_LEGS))
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()
    legs = args.legs.split(",")
    unknown = [leg for leg in legs if leg not in ALL_LEGS]
    if unknown:
        raise SystemExit(f"unknown legs {unknown}, expected a subset of {list(ALL_LEGS)}")
    root = Path(args.root).resolve()
    runners = {"python": python_leg, "shell": shell_leg, "android": android_leg}

    failed = False
    all_units = []
    for leg in legs:
        try:
            units = runners[leg](root)
        except (BudgetExceeded, ProtocolError) as e:
            print(f"FAIL {e}")
            return 1
        all_units += units
        bad = [u for u in units if u["outcome"] in OUTCOME_FAIL]
        skipped = sum(u["outcome"] == OUTCOME_SKIP for u in units)
        print(f"{leg}: {len(units)} tests, {len(bad)} failed, skipped: {skipped}")
        for u in bad:
            print(f"FAIL {u['id']}\n{u['detail']}")
        failed |= bool(bad)
    wall = time.monotonic() - _start
    print(f"total: {len(all_units)} tests, skipped: {sum(u['outcome'] == OUTCOME_SKIP for u in all_units)}, wall {wall:.1f}s")

    if wall > TOTAL_BUDGET_S:
        print(f"FAIL wall time {wall:.1f}s exceeds {TOTAL_BUDGET_S}s")
        failed = True
    slow = sorted((u for u in all_units if u["seconds"] > PER_TEST_BUDGET_S), key=lambda u: -u["seconds"])
    if slow:
        print(f"FAIL {len(slow)} tests exceed {PER_TEST_BUDGET_S}s; slowest {SLOWEST_SHOWN}:")
        for u in sorted(all_units, key=lambda u: -u["seconds"])[:SLOWEST_SHOWN]:
            print(f"  {u['seconds']:.3f}s {u['id']}")
        failed = True
    hits = forbidden_words(root)
    if hits:
        print("FAIL forbidden 'sleep' / 'delay(' in test sources:")
        print("\n".join(f"  {h}" for h in hits))
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == CHILD_FLAG:
        run_child(sys.argv[2])
    else:
        sys.exit(main())
