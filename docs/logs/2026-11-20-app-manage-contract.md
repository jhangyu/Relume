# App Manage — convergence contract (frozen 2026-11-20)

Spec: `docs/superpowers/specs/2026-11-20-app-manage-design.md`. Plan with frozen interfaces and tasks: `docs/superpowers/plans/2026-11-20-app-manage-plan.md`. Base: `8854f15`. Only the user changes this contract.

## End state
The App Manage page shows every non-legacy device's user apps against a chosen primary device and queues install, upgrade and remove actions that ADB devices and companion phones execute and report back, with all gates green on main.

## In scope
1. Migration 024 (`device_apps`, `device_app_reports`, `device_actions`, job kind `appmanage`) and the store module.
2. Pure matrix logic: row order, highest version, outdated flag, shown action per cell.
3. ADB inventory reader (user packages, version names, `dumpsys diskstats` sizes), `adb_pipe`, `ota.install_ref`.
4. Play delivery URL resolution with the existing apkeep fork and the ABI profile mapping.
5. Web page `appmanage.html` per the dense-grid mockup, sidebar entry, SSE topic `appmanage`.
6. Web routes (matrix, primary, create/cancel/dismiss actions, refresh, icons) and companion routes (icons, action start, action result), sync inventory ingest and action pickup.
7. ADB job kind `appmanage` (build, `play_direct` streamed, remove, `play_page`, inventory refresh).
8. Companion: inventory report with sizes (usage access), icon upload, 15-minute check interval, action executor (`build`, `play_direct`, `remove`, `play_page`) with result reports.
9. Device delete and adopt handling for the new tables; primary device setting.
10. Committed tests for the core logic listed in the plan §3; live check list document.

## Out of scope
- In-app Play client on the companion (the fallback if the live cross-IP check shows IP-bound URLs; separate follow-up).
- Dark theme.
- Per-locale or per-density apkeep profiles; new apkeep profiles of any kind.
- Labels and icons for ADB-only apps.
- Push channel to the companion (latency stays the sync interval; 15-minute option only).
- Parallel ADB action execution across devices.
- Changes to the default companion check interval.
- UI, browser or screenshot tests.
- Releases (server image, companion tag) and redeploys: the user runs them.

## Acceptance conditions
1. `local/webui/schema/024_appmanage.sql` matches plan §1.1; `(cd local && python3 -m unittest webui.tests.test_core_db webui.tests.test_appmanage_store)` exits 0.
2. `(cd local && python3 -m unittest webui.tests.test_appmanage_matrix)` exits 0 and covers row order, highest version and outdated rules of plan §1.7.
3. `(cd local && python3 -m unittest webui.tests.test_appmanage_store)` covers replace, cancel, dismiss, pickup-once, start/finish transitions, receipt finish, assignment write/delete, `move_device`, delete cascade.
4. `(cd local && python3 -m unittest webui.tests.test_appmanage_sync)` exits 0: full ingest, diff ingest, stale `base_rev` → `accepted=false`, old-app body ingests nothing, legacy row ignored, adopt moves rows.
5. `(cd local && python3 -m unittest webui.tests.test_appmanage_api)` exits 0: a device token gets 403 on every web App Manage mutation, 404 on another device's action start/result, legacy token 403 on `/api/me/icons`.
6. `(cd local && python3 -m unittest webui.tests.test_appmanage_adb webui.tests.test_appmanage_play webui.tests.test_appmanage_job)` exits 0.
7. `grep -n "'appmanage'"` finds the topic in `events.py`, the module and `CHANGES` entries in `server.py`, and the kind in `jobs.py`; `grep -rn "token=" local/webui/appmanage*.py` finds nothing.
8. `python3 scripts/test.py` (all legs, `ANDROID_HOME` set) exits 0 on the merged main: total ≤ 90 s, no test > 1 s, skip count 0.
9. Linux gate from `CLAUDE.md` exits 0 on the merged main.
10. `python3 scripts/ci_local.py` exits 0 on the merged main (guard + `assembleRelease`).
11. `local/webui/static/appmanage.html` and `appmanage.js` exist; `node --check local/webui/static/appmanage.js` exits 0; `app.js` sidebar links `/appmanage.html`.
12. Android `InventoryDiffTest` and `ActionRulesTest` run green in the android leg; the manifest declares `PACKAGE_USAGE_STATS`; `CheckInterval` has `M15`.
13. `docs/logs/2026-11-20-app-manage-live-checks.md` lists the 12 live checks of plan §4 with an empty result column, for the user to run.

## Round budget
2 rounds (round 1: 6 tasks, round 2: 5 tasks). Budget used up with any condition open → stop and report the failure trail; no third round without the user.

## Parking lot
