# Marketplace round-2 review (fresh reviewer)

Reviewed every commit `0d55fc3..3d18f2a` (26 commits). Code state for every gate: HEAD `3d18f2afabc9e12ceef3c006966b53c93abfd45c`, `git status --porcelain -- local src scripts android` empty. Raw artifacts (command output plus self-captured `RC=$?` plus HEAD, written by the commands) are in `.claude/tmp/mp-r2rev/`. Commands joined with the shell AND operator are written below as "then" (the bash hook rejects the ampersand character in file writes).

**Verdict: fix blockers first.** One code blocker (Android Marketplace list) and one failing contract gate (item 6, removed-symbol greps). Every other gate passes.

## Gates (contract items 2–10)

| Item | Command | RC | Decisive output | Artifact |
|---|---|---|---|---|
| 2 Linux gate | contract item 2 command verbatim (`docker build -f local/Dockerfile --target app -t relume-gate .` then `docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell`) | 0 | `python: 374 tests, 0 failed, skipped: 0` · `shell: 21 tests, 0 failed, skipped: 0` · `total: 395 tests, skipped: 0, wall 7.6s` | `linux.log` |
| 3 Android leg | `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` | 0 | `android: 34 tests, 0 failed, skipped: 0`; XML reports written 14:14 by this run (test.py deletes old reports first) | `android.log` |
| 4 Test inventory | `grep -rn "def <name>\b" local/webui/tests` per plan §3 name (39 Python names), `grep -rn "fun <name>" android/app/src/test` (3 names); ran-ids: `docker run … relume-gate python3 -m unittest -v <the 8 plan §3 modules>` | 0 | every name: 1 definition and 1 `… ok` line (109 tests, `OK`); Android `marketplaceListParses`, `appObjectParses`, `scheduleParses` in this run's XML reports; `test-build-item.sh` fixture has `"version": {"value": "1.0.0", "near": false}`, no `runner_version_unresolved` | `ids.log` |
| 5 Deleted files | `ls <each of the 8 plan §3 "Delete" paths>` | 1 ×8 | 8 × "No such file or directory" | `del-grep.log` |
| 6 Removed-symbol greps | the 10 commands of plan §4 | **FAIL** | greps 1, 4, 6–10 exit 1; **grep 2, 3, 5 exit 0** (hits below) | `del-grep.log` |
| 7 Live migration | `rm -rf .claude/tmp/mp-migrate/data` then `mkdir -p .claude/tmp/mp-migrate/data` then `sqlite3 local/data/webui.db ".backup .claude/tmp/mp-migrate/data/webui.db"` then `python3 .claude/tmp/mp-migrate/check.py .claude/tmp/mp-migrate/data --boot` | 0 | 22 `ok` migration lines incl. `user_version 10`, `foreign_key_check empty`, `8 upstream rows (1, 2, 19, 20, 23, 24, 25, 26)`; `ALL OK` | `migrate.log` |
| 8 Boot smoke | same command (`--boot`) | 0 | `ok boot() returned`; refresh `296 bundles, 0 skipped`; `GET /api/marketplace?per_page=1 -> 200`, `total >= 1`; `GET /api/apps -> YouTube, Reddit`; `schedule spec.every == day` | `migrate.log` |
| 9 Render | `python3 .claude/tmp/mp-web/render.py .claude/tmp/mp-migrate/data / /apps.html /schedules.html /builds.html /devices.html /projects.html /settings.html` | 0 | `ok` for all 7 paths | `render.log` |
| 10 Round-2 review | this file | **FAIL** | 1 code blocker (B1) | — |

Item 6 hits:
- grep 2: `local/webui/server.py:364` comment `(PUT /api/groups/order before PUT /api/groups/{id})` — stale comment, real hit.
- grep 3: `local/webui/catalog.py:26` comment `replaces source.cli_upstream_id` — revision note, real hit; `local/webui/market.py:214,251` `def list_sources` — the spec §6.5 handler of `GET /api/sources`, its name collides with the removed symbol; `local/webui/tests/test_projects.py:71,73` — `test_v4_database_migrates` seeds a v4 schema that has `source`/`source_id` by design (the reason `test_core_db.py` is excluded).
- grep 5: `local/webui/devices.py:142` `(compressed)` — substring false positive of the pattern `pressed`.

Item 7 note: `docs/logs/2026-10-26-mp-migration-check.md` records `b5c3d49`; `git diff b5c3d49 HEAD -- local/webui/schema local/webui/db.py` is empty, `check.py` sha1 is unchanged (`9534cce…`), and the rerun above is at HEAD.

Audit notes: the round-1 gate log (`3968a1d`) has no self-captured RC artifact and code changed after it, so items 2, 3, 7 and 8 were rerun, not reused. Item 9: I could not prove the instrument sees a console error (a headless-Chrome probe with a throwing page was denied by the sandbox); the `<main` and RC parts of the check are visible in the script. Item 4: `scripts/test.py` prints counts only, so the ids come from a verbose run in the same Linux image at the same HEAD.

## Findings

### Blockers

### [High] B1 Android Marketplace lists only the first 100 apps by name and computes the featured row from them

Location: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/MarketModel.kt:88`; `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/MarketplaceScreen.kt:69`; `android/app/src/main/kotlin/io/github/jhangyu/relume/api/RelumeApi.kt:297`
Dimension: Spec conformance (plan §6 dimension 1)
Rule: spec §8.3 (Marketplace list), §2.7 (featured from `market_apps()`)
Severity: High (evidence: the live catalog has 1205 apps (`migrate.log`), `per_page` is 100, the list is sorted by name, and no page parameter is ever passed: `grep -n "page" MarketModel.kt MarketplaceScreen.kt` returns nothing)
Origin: introduced
Tags: none
Register: none, Ticket: TBD, Owner: TBD

Evidence:
`load()` calls `marketplace(query, category, null, null, null)`, so page 1 only. `featuredApps(list.apps)` then picks the "top by bundles", built-in and "recent" apps from that alphabetical slice only.

Impact:
Unfiltered browsing on the phone shows about 8% of the catalog. Apps such as YouTube ("Y") are reachable only by search, and the featured carousel shows the most-bundled app among the first 100 names, not the catalog's. The web page pages correctly (`index.js` "More"), so the two clients disagree.

Recommended Fix:
Load more pages at the end of the `LazyColumn` (keep `page`, append to `list.apps` while `apps.size < total`), and compute the featured row as web does from server queries (`sort=bundles`, `origin=builtin`, `sort=updated`, small `per_page`).

### [Medium] B2 Contract item 6 (removed-symbol greps) fails

Location: `local/webui/server.py:364`, `local/webui/catalog.py:26`, `local/webui/market.py:214`, `local/webui/tests/test_projects.py:71`, `local/webui/devices.py:142`
Dimension: Techdebt
Rule: contract item 6 / project dead-code rule
Severity: Medium (a frozen acceptance gate fails; the code impact of the real hits is cosmetic)
Origin: introduced for server.py:364 (orphaned by the group-route removal), catalog.py:26 and market.py:214 (new lines); pre-existing for test_projects.py:71 and devices.py:142
Tags: needs-user-decision
Register: none, Ticket: TBD, Owner: TBD

Evidence: see "Item 6 hits" above.

Impact: the contract end state cannot be signed while the gate prints hits.

Recommended Fix:
Code side (implementer): delete the comments at `server.py:364` and `catalog.py:26`; rename `market.list_sources` to `get_sources` (route path unchanged). Gate side (lead or user, the contract is frozen): grep 3 must also exclude `test_projects.py` (its v4 migration fixture names the old tables, like `test_core_db.py`), and grep 5 must match a word (`\bpressed\b`). Without the gate change the two remaining hits cannot go without breaking a kept migration test.

### Should-fix

### [Medium] S1 The update run's outcome is not visible anywhere on the web

Location: `local/webui/static/builds.js:20`, `local/webui/static/builds.js:128`, `local/webui/static/schedules.js:55`, `local/webui/static/schedules.html:45`
Dimension: Spec conformance
Rule: spec §7.5 ("pipeline with the last run time and result"), §7.6 (retry of upstream jobs by target)
Severity: Medium
Origin: introduced
Tags: none
Register: none, Ticket: TBD, Owner: TBD

Evidence: Builds asks `/api/jobs?kind=build` only; the Schedules pipeline is static markup plus `Last run <time>`; no page reads an `upstream` job with target `{all}` or `{item_id}`.

Impact: a failed scheduled run ("catalog: …; upstream 7: token rejected", a blocked lookup) shows nowhere on the web; the user only sees that no build happened. Project rule: fail loudly.

Recommended Fix: in `schedules.js` load the newest upstream job whose `schedule_id` is the update row and show its status, `reason` and a log link under the pipeline; in `builds.js` list `upstream` jobs too and map Retry per spec §7.6 (`{item_id}` → `/check`, `{all}` → `/api/schedule/run`, `{upstream_id}` → `/api/projects/{id}/pull`).

### [Medium] S2 `inputs_sha` is taken before the runner refreshes the APKMirror version list

Location: `local/webui/build.py:118`, `local/webui/build.py:130`, `local/webui/apps.py:523`, `src/build/utils.sh:218`
Dimension: Data integrity
Rule: apps part §4.3 rebuild rule
Severity: Medium (spec-conformant: apps part §4.3 says "written into build_jobs at job start")
Origin: introduced
Tags: needs-user-decision
Register: none, Ticket: TBD, Owner: TBD

Evidence: for `latest` mode (and `auto` with no compatible list) the fingerprint's version is `apkmirror_versions[0]` read at job start; the runner's APKMirror page 1 emits `::apkm_versions::`, which `build._Markers.feed` writes to `app.apkmirror_versions` during the build.

Impact: when the list was empty or older at job start (first build of a latest-mode app, a manual Rebuild), the stored sha carries the old version; afterwards `needs_build` is true, the app shows "Update available", and the next run rebuilds the same APK once.

Recommended Fix: compute `apps.inputs_sha(item_id)` again after the runner succeeded, before `_register` writes the artifact rows, and store that value (or use `result["app_version"]` as the fingerprint version for the modes where `version.value` is null).

### [Low] S3 Dead interfaces with no production caller

Location: `local/webui/catalog.py:626` (`bundle_versions`, no caller at all), `local/webui/apps.py:13` (`STATES`, unused), `local/webui/apps.py:527` (`want_version`, test caller only)
Dimension: Techdebt
Rule: project rule "dead code: zero tolerance"
Severity: Low
Origin: introduced
Tags: none
Register: none, Ticket: TBD, Owner: TBD

Evidence: `grep -rnw "bundle_versions\|STATES" local/webui/*.py local/webui/static/*.js local/webui/tests/*.py` → definitions only; `want_version` → `apps.py:527` and `test_apps.py:70-78`.

Impact: the plan listed them as produced interfaces, but nothing consumes them.

Recommended Fix: delete `bundle_versions` and `STATES`; let the test assert `apps.job_input(id)["version"]` only and drop `want_version`.

### Nits

### [Low] N1 Web featured banners show one app per kind

Location: `local/webui/static/index.js:56-63`
Rule: spec §2.7 ("top 3 by bundle_count, the built-in apps, and the 3 most recently updated")
Origin: introduced. The web shows 1 + 1 + 1 cards (the mockup has 3 banners); Android shows the full set. Fix: align both clients on one reading and note it in the spec.

### [Low] N2 `throttle` holds the module lock during its wait

Location: `local/webui/apklocate.py:188-195`, `local/webui/apklocate.py:323`
Origin: introduced. `reset_lookup` (HTTP thread of `POST /api/apps/{id}/locate`) takes the same `_lock`, so the request blocks up to `MIN_INTERVAL` (10 s) while a lookup waits. Fix: compute the wait under the lock, wait outside it, then re-check and record under the lock.

### [Low] N3 PUT on a disabled app with a first URL answers 409 after saving

Location: `local/webui/market.py:142-146`
Origin: introduced. `apps.check_update` raises `409 app_disabled` after `put_download` wrote the URL. Fix: call `check_update` only when the app is enabled.

## Parking lot (not blockers)

- Contract parking item "Android DTOs guessed in f5e23ff": checked against `market.py`/`apps.py`/`catalog.py` output at HEAD — field names and nullability of `MarketApp`, `MarketDetail`, `BundleOption`, `AppObject`, `DownloadState`, `PatchView`/`PatchOption` (live `bundle_info` options all have `default,key,title,type,values` with string values), `Schedule`, `OtaSchedule`, `CustomSource` match; `MarketParseTest` covers them.
- `run_update` aborts the whole run if `apklocate.locate` raises anything other than cancel (`apps.py:487-489`; possible only on an `OSError` from `jobs.open_runner_file`, i.e. a runner leftover link). Consider catching it per app like the upstream step.
- `market.get_marketplace_app` aggregates all of `market_apps()` to read one package (`market.py:95`); fine at 1.2k apps.
- Lead note: Settings artifact estimate counts one APK per app (no ABI list in `/api/apps`).
- UI dimension 7 (mockup fidelity) is left to the two front-end reviewers (contract item 11).

## Pass 2 (reviewer-r2 at 77d425a; written by the lead from the reviewer's report, because a hook stopped the reviewer's commands)

Rechecked: B1 fixed (4928572), B2 fixed (7256fa3 + plan grep fix f3b2962), S1 fixed (d380a85), S2 fixed (build.py recomputes the fingerprint when version.value is null; new test), S3 fixed (7256fa3).

New blocker B3: 7256fa3 renamed test_want_version_modes to test_job_input_version_modes, after want_version was deleted under S3, while plan §3 still named the old test. Resolution by the lead: plan §3 now names test_job_input_version_modes. Lead check at 77d425af64b8d44129494d777342b0fa9cf4f4cb: all 39 required plan §3 names have a def, and each appears in the reviewer's pass-2 run logs (.claude/tmp/mp-r2rev/p2/). The 4 other names listed under plan §3 "Delete" are correctly absent.

Gates at 77d425a (logs with RC and HEAD in .claude/tmp/mp-r2rev/p2/):

| Contract item | Result |
|---|---|
| 2 Linux gate | PASS, RC=0, 396 tests, skipped 0, wall 7.0 s |
| 3 Android leg | PASS, RC=0, 36 tests |
| 4 Plan §3 tests | PASS after the plan rename (lead check above) |
| 6 Removed-symbol greps | PASS, 10 of 10 RC=1 |
| 7 and 8 Live migration and boot smoke | PASS, RC=0, ALL OK |
| 9 Render check | PASS, RC=0, 7 pages |

Verdict after B3 resolution: no open code blocker. Contract item 11 (front-end mockup reviews) is still open.
