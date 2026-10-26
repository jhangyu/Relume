# Marketplace round-1 gate

HEAD `3968a1d33455d1660efbe1e8fe4148c776f9a4f5` (all round-1 commits; working tree clean for local/ android/ src/ scripts/). Run 2026-10-26 by the lead.

| Check | Command | RC | Decisive output |
|---|---|---|---|
| macOS suite | `python3 scripts/test.py --legs python,shell` | 1 | 397 tests, skipped 6, wall 5 s. All failures are the known macOS environment ones: no setpriv (isolation tests), os.killpg EPERM (2 jobs tests), BSD realpath without -e (13 shell runner cases) |
| Linux gate | `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell` | 0 | 397 tests, skipped 0, wall 7 s |
| Android leg | `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` | 0 | 26 tests, 0 failed, skipped 0 |
| Live migration check | `sqlite3 local/data/webui.db ".backup <scratch>"` then `check.py <scratch>` | 0 | ALL OK |
| Boot smoke | `check.py <scratch> --boot` | 0 | refresh 296 bundles / 1205 apps; marketplace 200; apps = YouTube, Reddit; schedule every day; ALL OK |

The first boot-smoke attempt passed `--boot` before the data dir and failed with "user_version expected 9, got 0". That was an operator argument-order error; the rerun with the documented usage passed.
