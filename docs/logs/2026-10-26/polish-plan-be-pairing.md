# Plan: spec requirement 3, Devices page stays "pending" after an install

## Root cause (verified in code, HEAD 3abe409)
1. The "pending" badge is computed by comparing what the server installed (`ota_installs`) with the server's stored copy of the phone's package list (`device_packages`) (`local/webui/ota.py:171-176`, planner rules `ota.py:132-139`, project `ota.py:92-95`).
2. An ADB install job reads the phone's package list once, before it installs anything (`ota.py:665`, `_present` at `ota.py:215-226`), and after each install it only writes `ota_installs` (`_record`, `ota.py:511-521`), so the stored list still says "not installed" or "old version".
3. The page then sees "missing on phone" or "changed on phone" and shows pending until the next install job re-reads the phone; that job then repeats the same mistake for whatever it installs.

## Install paths checked
| Path | Route | Covered by fix |
|---|---|---|
| "OTA now" on a device | `ota_now` `ota.py:704-721` -> job `ota` -> `handle` | yes |
| Install after a build (`ota_after_build`) | `on_build_succeeded` `ota.py:692-697` -> `handle` | yes |
| Followed project "Install to phone" | `install_project` `ota.py:724-744` -> `handle` | yes |
| Recovery (force reinstall / downgrade) | `ota.py:759` -> `handle` (skips the pre-read today, `recovery or ...` at `ota.py:665`) | yes |
| Android Relume app receipt | `post_install` `ota.py:435-457` writes `device_packages` itself (`ota.py:453-455`) | already correct, not touched |
| Android app full report | `sync` -> `store_reported` `ota.py:378` | already correct, not touched |

All ADB installs go through `handle`; that is the one place to fix.

## Decision: re-read the phone after the run, do not write the expected versionCode
Writing the artifact's versionCode on "success" would record what the server expected, not what the phone has. If adb prints Success but the phone did not change (or a different package/version ended up installed), the badge would show "up to date" while it is wrong. Re-reading `pm list packages` after the steps stores the phone's real state; the existing drift rule (`_drifted`, `ota.py:99-102`) then still flags a real mismatch. Cost: one extra `adb shell pm list packages` per job. `post_install` stays as is: the Android app has no server-side way to re-read the phone.

## Exact edit (one file: `local/webui/ota.py`, function `handle`, lines 665-675)
Current:
```python
    steps = recovery or _plan_for(dev, item_ids, project_ids, _present(dev, job.cancel_event))['steps']
    ok, failed = [], set()
    for step in steps:
        if job.cancel_event.is_set():
            return
        log.line(...)
        if step['kind'] == 'project':
            ok.append(_run_project(...))
        else:
            ok.append(_run_item(...))
    outcome = ...
```
New:
```python
    steps = recovery or _plan_for(dev, item_ids, project_ids, _present(dev, job.cancel_event))['steps']
    ok, failed = [], set()
    for step in steps:
        if job.cancel_event.is_set():
            break
        log.line(...)                      # unchanged
        if step['kind'] == 'project':
            ok.append(_run_project(...))   # unchanged
        else:
            ok.append(_run_item(...))      # unchanged
    _present(dev)  # store what the phone really has now: the pre-run read is stale after any install
    if job.cancel_event.is_set():
        return
    outcome = ...                          # unchanged
```
Notes:
- `_present(dev)` is called without `cancel_event` so a cancelled job still stores the installs that already happened (bounded by the existing 60 s adb timeout in `_present`).
- If the re-read fails (adb returns non-zero or times out), `_present` returns None and stores nothing; the page keeps showing pending until the next job. That is the honest "unknown" state, no silent default.
- No other file changes. No change to `_record`, `post_install`, the planner, or the web client (`devices.js` already refetches after each action and every 15 s).

## Tests
- No existing test must change. Checked `local/webui/tests/test_device_otajob.py`: its fake adb answers every `pm` call with the same rule (`online()`, line 47-49) and unmatched calls exit 0 with empty output; assertions filter adb calls by verb (`adb_cmds`, line 65) or count `connect` only (line 113); tests asserting `calls() == []` (lines 129, 398) never reach the step loop. The extra `pm` call is invisible to all of them. `test_device_ota.py` builds views directly and does not run `handle`.
- The existing suite cannot catch this bug: no test checks `device_packages` or `pending` after `handle` runs (preflight Part C confirms the gap). Add one test (real bug class, ≤1 s, about 8 fake-adb calls like its neighbours) to `test_device_otajob.py`:

```python
    def test_report_refreshed_after_install(self):
        self.fa.rule('devices', out=ONLINE)
        self.fa.rule('pm', out='package:com.android.settings versionCode:35\n', times=1)  # before: nothing installed
        self.fa.rule('pm', out=f'package:{YT} versionCode:1\npackage:{YTM} versionCode:1\n')  # after the installs
        self.fa.rule('install', out='Success\n')
        jid, err = self.run_job()
        self.assertIsNone(err)
        self.assertEqual(ota.reported_packages(self.d)[YT]['version_code'], 1)
        dev = ota.list_devices(None)[1]['devices'][0]
        self.assertEqual([a['pending'] for a in dev['assignments']], [False, False])
```
  It asserts items only: in this fixture the microG project exists only in a mock (`projects.project_info`), not in the `project` table, so `store_reported` drops its package (`tracked_packages`, `ota.py:50-55`). In production the project row exists, so projects are covered by the same code.
- Red first: run the new test on the unchanged code and see it fail (pending `[True, True]`, `reported_packages` has no YT), then apply the edit and see it pass. Keep the red/green output in a scratch log, not in the repo.

## Mechanical acceptance checks
1. `cd local && python3 -m unittest webui.tests.test_device_otajob.OtaJobTest.test_report_refreshed_after_install` fails before the edit, passes after (RC captured in a scratch log).
2. `python3 scripts/test.py --legs python,shell` green, inside the per-test 1 s cap (Linux gate command from `CLAUDE.md` for the final run).
3. `grep -n "_present(dev)" local/webui/ota.py` shows the new call after the step loop in `handle`; `git diff --stat` lists only `local/webui/ota.py` and `local/webui/tests/test_device_otajob.py`.
4. Optional live confirmation by the user: after one "OTA now" on an ADB phone, the device card shows no pending badge for the installed apps.

## Uncertain
- That the user's "Pair devices stays pending" is this per-app badge on the Paired devices card: pairing itself has no pending state (`devices.py:327-372`). Live API needs login, so not confirmed on the running server.
- The exact unittest invocation path (module root `local/`) is inferred from imports `from webui import ...`; the implementer should use whatever `scripts/test.py` uses for a single test.
