#!/usr/bin/env python3
"""Fail when any GitHub workflow `run:` step runs tests (CI only builds, compiles and publishes).

Usage: ci_guard.py [workflows_dir]   (default: .github/workflows)

Stdlib only. PyYAML is not guaranteed in the runner's system python3 and nothing may be installed,
so `run:` values are extracted line-based: inline scalars and `|`/`>` block scalars.
"""
import os
import re
import shlex
import sys

DEFAULT_DIR = ".github/workflows"
RUN_KEY = re.compile(r"^(\s*)(-\s+)?run:\s*(.*)$")
NAME_KEY = re.compile(r"^\s*(?:-\s+)?name:\s*(.*)$")
BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*$")
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\$\(|\)|`")
TEST_SCRIPT = re.compile(r"(^|/)(test-[^/]*\.sh|[^/]*_test\.sh|test\.py)$")
TEST_RUNNERS = {"pytest", "py.test", "ctest", "unittest"}
GRADLE = {"gradlew", "gradle"}
PKG_MANAGERS = {"npm", "yarn", "pnpm"}
TEST_SUBCOMMAND_TOOLS = {"go", "cargo"}
OUTPUT_ONLY = {"echo", "printf"}
GRADLE_TEST_WORD = re.compile(r"test|^check$", re.IGNORECASE)


def extract_runs(lines):
    """Yield (step_name, line_no, command_text) for every run: value."""
    name = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*-\s", line):
            name = None
        m = NAME_KEY.match(line)
        if m:
            name = m.group(1).strip().strip("\"'")
        m = RUN_KEY.match(line)
        if not m:
            i += 1
            continue
        key_indent = len(m.group(1)) + (len(m.group(2)) if m.group(2) else 0)
        value = m.group(3).strip()
        label = name or f"line {i + 1}"
        if BLOCK_SCALAR.match(value):
            body = []
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > key_indent):
                body.append(lines[j].strip())
                j += 1
            yield label, i + 1, "\n".join(body)
            i = j
            continue
        yield label, i + 1, value.strip("\"'")
        i += 1


def offending(command_text):
    """Return the command segments that run tests."""
    joined = re.sub(r"\\\n\s*", " ", command_text)
    found = []
    for raw in joined.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        for segment in SEGMENT_SPLIT.split(raw):
            segment = segment.strip()
            if not segment:
                continue
            try:
                tokens = shlex.split(segment)
            except ValueError:
                tokens = segment.split()
            if tokens and runs_tests(tokens):
                found.append(segment)
    return found


def runs_tests(tokens):
    if os.path.basename(tokens[0]) in OUTPUT_ONLY:
        return False
    base = [os.path.basename(t) for t in tokens]
    if any(TEST_SCRIPT.search(t) for t in tokens):
        return True
    if any(b in TEST_RUNNERS for b in base):
        return True
    if "unittest" in tokens or any(a == "-m" and b == "unittest" for a, b in zip(tokens, tokens[1:])):
        return True
    if base[0] in GRADLE or any(b in GRADLE for b in base[:2]):
        return any(GRADLE_TEST_WORD.search(t) for t in tokens[1:] if not t.startswith("-"))
    if base[0] in PKG_MANAGERS:
        return "test" in tokens[1:3]
    if base[0] in TEST_SUBCOMMAND_TOOLS:
        return len(tokens) > 1 and tokens[1] == "test"
    return False


def main():
    directory = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    files = sorted(
        os.path.join(directory, f) for f in os.listdir(directory) if f.endswith((".yml", ".yaml"))
    )
    if not files:
        sys.exit(f"ci_guard: expected workflow files in {directory}, found none")
    violations = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for step, line_no, text in extract_runs(lines):
            for cmd in offending(text):
                violations.append(
                    f"CI runs no tests; {os.path.basename(path)}:{line_no} step '{step}' runs: {cmd}"
                )
    if violations:
        print("\n".join(violations), file=sys.stderr)
        sys.exit(1)
    print(f"ci_guard: {len(files)} workflow files, no test commands")


if __name__ == "__main__":
    main()
