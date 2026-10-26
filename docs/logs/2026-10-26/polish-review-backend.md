# Polish batch — backend review (7cbb28e, 1a78105)

Scope: local/webui/{ota,projects,market}.py, tests/test_market_api.py. Dimensions: correctness, architecture.
Verdict: **merge-ready**. No blocker, no should-fix. 2 nits.

## Checks passed
- Contract: `projects.followed_apps` (projects.py:269-285) returns exactly the keys in the plan's API contract table.
  `blocked` uses the same `_alert` rule as `install_project` (ota.py:727) and `download_apk` (projects.py:505), so a blocked app cannot be downloaded or installed by another route.
  `device_ids` lists ADB phones only (projects.py:263-266). `list_apps` (market.py:109-111) leaves `apps[]` unchanged. Android `AppList` reads both arrays (Models.kt:573).
- One URL formula: the only "apks/" strings are `apk_url` (projects.py:91) and the route itself (projects.py:523). ota.py no longer imports urllib. The new market→projects import makes no cycle (no module imports market).
- Auth: `GET /api/apps` and `GET /api/projects/{id}/apks/{file}` accept a session or a device token; neither is in `auth.SESSION_ONLY` (auth.py:20). Web Install uses `POST /api/projects/{id}/install`, which is unchanged.
- `handle` cancel path (ota.py:666-676): the loop breaks, the phone is re-read, then the job returns as before. Installs that already finished are stored.
- adb reports Success but the phone did not change: for built apps the re-read either drops the package ("missing on phone") or keeps the old versionCode, and `_drifted` (ota.py:98-102) marks it pending. That works. For Followed projects see parking lot 1.
- No step function raises, so the re-read always runs before `_finish`/`_fail`.

## Nits
### Nit 1: cancelling a job can take up to 60 s longer
- Location: ota.py:674.
- Problem: the re-read `_present(dev)` runs without the cancel event, so a cancelled job or an unreachable phone waits for the full 60 s adb timeout. It also runs when no install was attempted: everything was up to date, or the job was cancelled before the first step.
- Fix: re-read only when at least one install was attempted. For example, set `attempted = True` in the step loop and call `if attempted: _present(dev)`. Keep the cancel event off, as the plan intends.

### Nit 2: `GET /api/apps` can return 500 if a project is deleted at the same moment
- Location: projects.py:272-273.
- Problem: `followed_apps` selects the project ids first and then calls `_row(pid)` once per project. If `delete_project` runs in between, `_row` returns None and `r["downloaded_tag"]` raises TypeError. That gives a 500 on the Apps list (web and Android) and the web badge. The window is small and the next load works.
- Fix: `if r is None: continue`, or build the list from one JOIN query.

## Parking lot (no severity, not introduced)
1. Followed projects have no version-drift rule. In `_project_action` (ota.py:85-95), adb Success without a real version change on the phone still shows the project as up to date; only a missing package is caught. The plan's sentence "the drift rule still flags a real mismatch" holds for built apps only.
2. A device token can call `POST /api/projects/{id}/install` with an empty body and start installs on every ADB phone assigned to the project. The route is not session-only. Android does not call it.

## Evidence audit (gate not rerun)
- `.claude/tmp/polish-web-followed/gate.log`: python 370 and shell 21, 0 failed, 0 skipped, RC=0, written at 16:21:21.
- The artifact has no commit hash. Code state checked another way:
  - `git diff 1a78105 HEAD -- local/ scripts/` is empty.
  - `git status` for local/ and scripts/ is clean.
  - No file under local/webui or scripts is newer than that log.
  - So the gate ran on today's backend code (1a78105, which also contains 7cbb28e).
- `.claude/tmp/polish-be-apps/check.log` RC=0. Its `check.py` checks the contract keys, file order, the URL with a space in the name, blocked giving `files == []`, a null package giving a null icon_url, and that `list_apps` returns both keys.
