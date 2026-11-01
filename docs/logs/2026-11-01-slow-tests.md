# Slow webui tests near the 1 s cap: root cause and fix (2026-11-01)

Host: Apple M3 Ultra, 28 cores (20 P + 8 E), macOS APFS, load average 4–9 during every run. Base 8014818.
Probe scripts lived in `scripts/tmp/slow/` (scratch, gitignored, deleted after sign-off).

## Root causes, with numbers

1. **Each test spent half its time on file-system metadata work, and that work does not scale across processes.**
   - Serial, by phase (all 442 tests, `_callSetUp` / test body / `doCleanups`): setUp 3.37 s + cleanup 1.53 s of
     10.37 s in total. At 14 workers: setUp 11–12 s + cleanup 5–6 s of 27–31 s.
   - Cost per `DataTestCase` test: `mkdtemp` in `$TMPDIR`, `copytree` of the template (8 dirs, `copystat` each:
     `utime`/`chmod`/`chflags` ×8), 7 extra `mkdir` calls from `db.init` (EEXIST), then `rmtree`. Profile of
     the whole suite at 14 workers: 6886 `mkdir` = 3.39 s, 3525 `rmdir` = 1.77 s, `open` 2.52 s, `scandir` 1.37 s,
     `utime`/`chmod`/`chflags` 3350 calls each.
   - Scaling of that work alone (mkdtemp + 8 subdirs + rmtree, P processes in parallel, ms per round):
     P=1 1.3 · P=2 2.1 · P=4 3.7–5.1 · P=7 10.7–11.4 · P=14 34.5–53.8, at 63–93 % system CPU (in-kernel lock
     contention). Same numbers with a private parent outside `$TMPDIR` (same APFS volume): it is volume-wide.
     A pure-Python CPU loop scales flat (0.40–0.49 ms per op at P=1…14): CPU count is not the limit.
   - `scripts/test.py` ran `cpu_count // 2` = 14 workers. Total test time: 8.7 s serial, 11–20 s at 7 workers,
     27–32 s at 14 workers, while the leg wall time stays about 4 s at 4…14 workers (the longest module,
     `test_device_otajob`, takes about 2 s on its own).

2. **Creating or removing an entry directly in the shared `$TMPDIR` stalled for up to 270 ms per call.**
   `$TMPDIR` (`/var/folders/.../T`) holds 36 124 entries (29 669 `tmp*`, mostly other projects' build leftovers).
   Traced single test, serial, nothing else of ours running
   (`test_apps.EnableTest.test_enable_new_and_reenable_keep_overrides`): `mkdir T/tmp…` 267 ms and 92–120 ms,
   `rmdir T/tmp…` 94–198 ms; the same test with `TMPDIR` set to a small private dir: every call ≤ 0.09 ms,
   0.07 s for the test. Each run made about 900 entries there. Which process holds the directory was not
   proven; file-system scanners were running (Spotlight `mds`, DaisyDisk, FSearch).

3. **The production scrypt cost ran in 4 test modules.** `auth.SCRYPT_N = 2**14` (16 MiB per call): 22.6 ms
   serial, 43 ms at 14 parallel processes (memory bound). 184 calls in the suite, 1.71 s in total; `login()`
   costs two. 5 classes already patched it to `2**4`; `test_core_settings`, `test_core_sse`,
   `test_core_scheduler`, `test_ui_static` did not.

4. **One test waited 0.5 s on purpose.** `test_core_jobs.WorkerTest.test_timeout` used `timeout=0.5` on
   `tail -f /dev/null`, the slowest test of every serial run (0.52–0.60 s).

Checked and not a cause: HTTP server per test (no 0.5 s `serve_forever` poll, `run_server` exits at once),
`join`/`wait` timeouts (fail-fast upper bounds only), import time (~0.1 s per module, outside the tests),
python stub start (16 ms per interpreter, `-S`/`-I` gives nothing), schema migration (once per process in
`setUpClass`).

## Fix

- `scripts/test.py`: `PYTHON_WORKERS_MAX = 4` (the measured knee of the scaling table); a private `TMPDIR`
  (`relume-test-*`, mode 1777 like `/tmp`, so tests that drop to the runner uid still reach their dirs) for the
  python and shell legs, removed at the end of the run; the slowest-10 list is printed on every run.
- `local/webui/tests/helpers.py` `DataTestCase.setUp`: copy only `webui.db` (`db.init` makes the subdirs), and
  patch `auth.SCRYPT_N` to `FAST_SCRYPT_N = 2**4` for every test; the 5 per-class copies are removed.
  Production code is untouched: `auth.SCRYPT_N = 2**14` stays (`local/webui/auth.py:36`).
- `test_core_jobs.WorkerTest.test_timeout`: `timeout=0.05` (same assertion: the job fails with reason timeout).

## Results (python leg, same host load)

| | serial total | 14 workers total | slowest test |
|---|---|---|---|
| before (8014818) | 10.4 s (setUp 3.37) | 27–32 s | 1.02–1.30 s at 14 workers |
| after | 6.2 s (setUp 0.74) | – (4 workers: 8.6–12.4 s) | 0.27–0.52 s at 4 workers |

Gate runs: see the evidence records in the report to the lead (commands, RC, slowest-10 lists, commit hash).

## Not changed (candidates)

- `ImportCycleTest` starts one interpreter per module at once (about 40, 6.5 s of CPU in 0.5 s): a CPU burst
  that slows every test running beside it. Kept: it is the only cycle check.
- `test_device_otajob`, `test_playversion.SessionCase`: 16–30 ms per Python stub process (fake adb, fake
  apkeep); 0.2–0.45 s per test. A shell stub would be faster, but needs its own JSON rule matching.
