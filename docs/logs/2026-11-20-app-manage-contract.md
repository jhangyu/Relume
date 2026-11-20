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
11. Per-device locale for Google Play direct downloads (user decision 2026-11-20): the companion reports the phone's locale, ADB devices read it with getprop, and the Play URL lookup asks apkeep for that locale so the phone gets its language split. If the apkeep fork cannot select a locale, stop and report to the user.

## Out of scope
- In-app Play client on the companion (the fallback if the live cross-IP check shows IP-bound URLs; separate follow-up).
- Dark theme.
- Per-density apkeep profiles; new apkeep device profiles (the locale is a per-request option, not a new profile).
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
8. `python3 scripts/test.py` (all legs, `ANDROID_HOME` set) exits 0 on the merged main on macOS: total ≤ 90 s, no test > 1 s; the only skips are these 8 Linux/root-only tests (user decision 2026-11-20): test_core_jobs WorkerTest cancel_kills_process_group, on_line_raise_kills_process_group, success_kills_leftover_group_members; test_build_service HandlerFailTest start_keeps_foreign_owned_apk_cache_mode and IsolationTest runner_uid_paths_and_secret; test_catalog_inspect InspectRowTest inspect_runs_isolated; test_core_settings UiTokenTest token_modes_and_runner_denied; test_core_startup EmptyVolumeTest foreign_owned_dir_keeps_mode.
9. Linux gate from `CLAUDE.md` exits 0 on the merged main with skip count 0.
10. `python3 scripts/ci_local.py` exits 0 on the merged main (guard + `assembleRelease`).
11. `local/webui/static/appmanage.html` and `appmanage.js` exist; `node --check local/webui/static/appmanage.js` exits 0; `app.js` sidebar links `/appmanage.html`.
12. Android `InventoryDiffTest` and `ActionRulesTest` run green in the android leg; the manifest declares `PACKAGE_USAGE_STATS`; `CheckInterval` has `M15`.
13. `docs/logs/2026-11-20-app-manage-live-checks.md` lists the 12 live checks of plan §4 with an empty result column, for the user to run.
14. `(cd local && python3 -m unittest webui.tests.test_appmanage_play)` covers the locale passed to apkeep for an app row (reported locale) and an ADB row (getprop), and a missing locale fails loudly; the live check list has a check that a zh-TW phone gets a `config.zh` split.

## Round budget
2 rounds (round 1: 6 tasks, round 2: 6 tasks). Amended by the user 2026-11-20: in-scope item 11, acceptance 8, 9, 14. Budget used up with any condition open → stop and report the failure trail; no third round without the user.

## Parking lot
