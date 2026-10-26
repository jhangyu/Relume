# Marketplace refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use podium:team-spawn (recommended) or podium:team-fable to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "patch source × app" item model with a Marketplace of catalog apps, one enabled app = one chosen bundle, kept updated by one global schedule, on web (Fluent 2) and Android (Material 3), light theme only.

**Architecture:** One SQLite migration (010) re-keys the enabled app onto a `bundle` table filled from the community catalog, the built-in bundle and custom sources; new server modules `apps.py` (enabled apps, build inputs, update run), `apklocate.py` (APKMirror lookup) and `market.py` (all new routes); the web gets two new pages on a Fluent 2 stylesheet, the Android app gets an MD3 theme and three tabs.

**Tech Stack:** Python 3 stdlib server + SQLite (`local/webui/`), Bash build runner (`src/build/`), plain ES modules + CSS (no build step), Kotlin + Jetpack Compose Material 3 from Compose BOM 2026.09.00 (no new dependency).

**Decisions (hard-to-reverse only):**
- Enabled app stays the `item` row with its ids (alternatives: new `app_choice` table; re-key everything) · driver: artifacts, device assignments, OTA receipts and the shipped Android plan are keyed by `item.id` · drawback: the name `item` survives in SQL.
- Backend removals happen in round 1, inside the tasks that replace the code (alternative: keep old code until round 2) · driver: the old code reads tables that migration 010 drops, so it cannot stay green next to it · drawback: the old web pages and the old Android app call dead routes between round 1 and round 2 (unreleased project, accepted).
- One route module `market.py` for every new route (alternative: routes in each owning module) · driver: one owner per round-1 file, so the API task does not edit `apps.py`/`catalog.py` · drawback: `market.py` imports three modules.

Plan gate: `/Users/jhangyu/.claude/plugins/cache/jhangyu/podium/1.1.1/protocols/review.md` §L0 Plan gate.

Spec (single source of behaviour): `docs/superpowers/specs/2026-10-26-marketplace-design.md` (cited "spec §n"). The spec points into the three part files `docs/superpowers/specs/parts/2026-10-26-mp-backend-{catalog,apps,api}.md` (cited "catalog part", "apps part", "API part"); the spec wins on conflict.
Base: main @ `0d55fc3` (mockup files under `docs/superpowers/mockups/marketplace/candidate-2/android/` may have later uncommitted colour edits by the designer; do not edit them).

## 0. Convergence contract (draft for the lead to freeze)

**End state.** The spec's server, migration, web and Android changes are on main, the Python + shell suite passes on macOS and in the Linux gate within 90 s, the Android leg passes, migration 010 is verified on a backup copy of the live database, and the round-2 reviewer reports zero blockers; release, redeploy and the new companion release are then the user's steps.

**In scope.**
1. Server: migration 010, catalog layer, enabled-app model, build flow, update run, global schedule, APKMirror locator, HTTP API (spec §1–§6).
2. Removals and their tests and docs (spec §9, §10).
3. Web: Fluent 2 foundation, Marketplace, Apps, Schedules, restyled kept pages, light only (spec §7).
4. Android: MD3 fixed palette, three tabs, Marketplace, Apps with Install, App settings, Schedules, Settings behind the avatar (spec §8).
5. Live migration check on a `.backup` copy (spec §4, §11).

**Out of scope.** A second catalog (awesome-morphe); GitLab release download; cron schedules; `bundles.json` `featured[]`; release, Portainer redeploy, Clean up press, companion release (user, spec §11); on-device checks; any change to `android/tokens.md`, `android/m3.css` or other mockup files.

**Acceptance (mechanical; run on main after round 2 or the fix round).**
1. macOS suite: `python3 scripts/test.py --legs python,shell` → exit 0, `skipped: 0` per leg, wall time ≤ 90 s.
2. Linux gate: `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell` → exit 0, wall time ≤ 90 s.
3. Android leg: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` → exit 0.
4. Every committed test in plan §3 exists and ran: `grep -n "def <name>"` / `grep -n "fun <name>"` → one hit each, and its id appears in the gate 1/3 output.
5. Deleted files are gone: `ls` of every path in plan §3 "Delete" → "No such file".
6. Removed-symbol greps (plan §4) → no hit (exit 1).
7. Live migration check (plan §5): the scratch check script exits 0 on a fresh `.backup` copy of `local/data/webui.db` at the merged HEAD; log with command, self-captured RC and HEAD hash in `docs/logs/2026-10-26-mp-migration-check.md`.
8. Boot smoke on the migrated copy (plan §5): `boot()` returns; `GET /api/marketplace?per_page=1` (session) → 200 with `total` ≥ 1 after one `POST /api/catalog/refresh`; `GET /api/apps` → 2 apps (YouTube, Reddit); `GET /api/schedule` → `spec.every == "day"`.
9. Pages render: the scratch render check (plan §8) passes for `/`, `/apps.html`, `/schedules.html`, `/builds.html`, `/devices.html`, `/projects.html`, `/settings.html` (headless Chrome, page has `<main`, no `Uncaught` or failed resource in the log; run by the reviewer, plan §6).
10. Round-2 reviewer (fresh, opus): 0 blockers on the commits of rounds 1–2.

**Round budget.** 2 implementation rounds + at most 1 fix round. Budget spent with acceptance open → stop and report the failure trail to the user.

## 1. Rounds

| Round | Task | Name | Model | Scope (one line) |
|---|---|---|---|---|
| 1 | 1.1 | Migration and shared fixtures | opus | `010_marketplace.sql`, `db.SUBDIRS`, test fixture helpers, OTA/notify label SQL, device/project test fixtures, live migration check |
| 1 | 1.2 | Enabled apps and build flow | opus | new `apps.py`, `build.py`, `scheduler.py`, `jobs.py`, `__main__.py`, build shell scripts |
| 1 | 1.3 | Catalog layer | opus | rewrite `catalog.py`, trim `upstream.py`, `projects.py` pull route, `http_get` 4-tuple, delete the seed |
| 1 | 1.4 | APKMirror locator and HTTP API | opus | new `apklocate.py`, `market.py`, `fetch-page.sh`, `server.MODULES` |
| 1 | 1.5 | Android foundation | sonnet | MD3 theme + components + icons, new API client and models, three-tab navigation, port of kept screens, thin first Marketplace/Apps/Schedules screens |
| 2 | 2.1 | Web foundation and Marketplace | sonnet | `app.css`, `app.js`, `index.html/js` (Marketplace), `test_ui_static.py` |
| 2 | 2.2 | Web Apps page | sonnet | `apps.html/js` new, `item.html/js` deleted |
| 2 | 2.3 | Web Schedules, kept pages, docs | sonnet | `schedules`, `builds`, `devices`, `projects`, `settings`, `login` pages; `README.md`, FAQ |
| 2 | 2.4 | Android Marketplace | sonnet | Marketplace list, app detail, download-source step, custom source dialog |
| 2 | 2.5 | Android Apps, Install, Schedules | opus | Apps tab with Install row, App settings, Schedules, Settings screen |
| 2 | R | Reviewer | opus | review dimensions + full gates (plan §6) |

Order inside a round (all other work starts at once and codes against the interfaces in the task blocks):
- Round 1: task 1.1's **first commit** = `010_marketplace.sql` + `db.py` + `tests/catalog_rows.py` + `tests/device_helpers.py`. Tasks 1.2–1.4 run their own tests only after that commit. Task 1.2's **first commit** = `__main__.py` without `seed_import`/`export-seed` + `scheduler.ensure_update()`. Task 1.3 deletes the seed code only after that commit. Task 1.3's **first commit** = `upstream.http_get` 4-tuple (all callers) + `catalog.ensure_upstream`, `engine_cli`, `patch_list`, `bundle_versions`. Until a peer's function is committed, tests replace it with `unittest.mock.patch.object`. Each first-commit author sends the commit hash to the lead, who relays it.
- Round 1 end: lead's gate step (plan §5). Round 2 starts only on a green gate.
- Round 2: task 2.1's **first commit** = `app.css` + `app.js` (sprite, `icon()`, shell nav, dark mode removed) + `test_ui_static.py` updates. Tasks 2.2 and 2.3 use the class names of `web/fluent.css` (which `app.css` adopts unchanged) and render their pages only after that commit. Android tasks 2.4 and 2.5 depend only on round 1.
- Round 2 end: the reviewer runs the full gates (plan §6). Fix round (if needed): the lead re-dispatches only the failing tasks, with the reviewer's findings.

Between task 1.1's first commit and the round-1 gate, tests outside your own files may be red because peers are mid-change. Run only your targeted command; do not fix a peer's file — report it to the lead.

## 2. Rules for every task

- **Read first:** the spec sections named in your task, your task block here, and the code you change. Verify every claim of the spec in the code; report contradictions instead of guessing.
- **Ownership:** edit only files listed under "Files" of your task. Need a change in another file → stop and ask the lead.
- **Git (shared tree):** no `git stash` (also no `git stash list`), no `reset`, no `checkout --`, no `clean`, no `git add -A`/`git add .`. Commit with `git add <own files>` and `git commit -m "<msg>" -- <own paths>`; `git mv` lists both old and new path; deleted files go into the pathspec. Before a commit run `git rev-parse --abbrev-ref HEAD` (must be `main`); after it run `git status --porcelain -- <own paths>` (must be empty). No `;` inside `-m` text. Commit message ends with the attribution line the lead gives you.
- **Tests:** commit only core-logic, data-integrity, security or past-bug tests; each ≤ 1 s, hermetic (no network, no real clock, fixed seeds, no `sleep`/`delay(` — `scripts/test.py` fails on those words in test sources). Your targeted command must finish within 30 s. One-off checks (red-to-green proofs, live data probes) go to `.claude/tmp/mp-<task>/` and are not committed.
- **Commands:** Python targeted tests run from `local/` and write a log first, so the RC is the test's own: `mkdir -p /Users/jhangyu/project/relume/.claude/tmp/mp-<task> && cd /Users/jhangyu/project/relume/local && python3 -m unittest <modules> > /Users/jhangyu/project/relume/.claude/tmp/mp-<task>/t.log 2>&1; echo RC=$?`. Never pipe the test command into `tail`/`grep` when you need its RC. Android targeted command: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:testDebugUnitTest --tests '<pattern>' --console=plain --no-watch-fs`; if it does not finish in the foreground limit, stop and report BLOCKED (no background runs).
- **No sleep, no polling** in code or commands. Waiting for a peer = end your turn and wait for a message.
- **Error handling (project rules):** no swallowed exceptions, no silent fallback, every new error message names the object, the expected value and the actual value; validate at the boundary once.
- **Dead code:** delete replaced code with its tests and docs; no commented-out code, no unused functions.
- **Report** (to the lead, by SendMessage; the lead's dispatch names the recipient): conclusions with `file:line`, changed files, each acceptance item pass/fail with the command and RC, open points; last line `READY_FOR_SIGNOFF` or `BLOCKED`. Do not paste diffs.

## 3. Test inventory

Committed tests (each ≤ 1 s, hermetic). Owner task in brackets. Test method names are fixed here so contract item 4 can grep them.

| File | Required test methods | Task |
|---|---|---|
| `local/webui/tests/test_core_db.py` | `test_migration_010_keeps_builtin_items_and_drops_the_rest`, `test_migration_010_fresh_db_has_builtin_bundle` | 1.1 |
| `local/webui/tests/test_apps.py` (new) | `test_job_input_version_modes`, `test_needs_build_matrix`, `test_update_run_continues_after_catalog_failure`, `test_update_run_skips_app_without_url`, `test_enable_new_and_reenable_keep_overrides`, `test_bundle_change_keeps_same_name_overrides`, `test_enable_with_unknown_patch_is_400`, `test_put_patches_validates_types` (ported), `test_version_settings_validation` (ported), `test_module_bundle_variant_and_engine_rules` (ported), `test_job_json_has_version_value_not_mode` | 1.2 |
| `local/webui/tests/test_core_scheduler.py` | `test_update_schedule_cannot_be_created_or_deleted`, `test_update_schedule_fires_one_all_run`, `test_ensure_update_creates_missing_row` (+ existing next_run / ota tests) | 1.2 |
| `local/webui/tests/test_build_service.py` | `test_cleanup_plan_lists_orphan_artifact_and_upstream_dirs` (+ kept marker/artifact/retention/isolation tests) | 1.2 |
| `local/webui/tests/test_catalog_feed.py` (new) | `test_parse_official_accepts_all_dialects`, `test_parse_official_rejects_bad_root`, `test_parse_official_skips_bad_bundle_and_records_it`, `test_refresh_keeps_last_good_copy`, `test_apply_keeps_ids_and_chosen_bundles`, `test_dedup_prefers_builtin_and_custom`, `test_icon_fetch_refuses_foreign_host_redirect_and_size`, `test_parse_patches_list_rows_and_universal`, `test_add_custom_module_rules`, `test_add_custom_without_patches_list_is_refused` | 1.3 |
| `local/webui/tests/test_catalog_upstream.py` | `test_http_get_returns_final_url_and_caps_body`, `test_prune_deletes_bundle_info_of_pruned_tags` (+ kept check/pull/download/inspect tests) | 1.3 |
| `local/webui/tests/test_apklocate.py` (new) | `test_parse_search_candidates`, `test_play_id_and_missing_block`, `test_decide_found_ambiguous_not_found_blocked`, `test_throttle_waits_and_cooldown_blocks`, `test_validate_list_url` | 1.4 |
| `local/webui/tests/test_market_api.py` (new) | `test_marketplace_filter_sort_page`, `test_marketplace_bad_per_page_is_400`, `test_enable_201_and_conflicts`, `test_build_without_url_is_409`, `test_device_token_reaches_new_routes` | 1.4 |
| `src/build/test-build-item.sh` | existing runner cases with `.version = {value, near}` | 1.2 |
| `android/app/src/test/kotlin/io/github/jhangyu/relume/api/MarketParseTest.kt` (new) | `marketplaceListParses`, `appObjectParses`, `scheduleParses` | 1.5 |

Delete (file):
`local/webui/tests/test_catalog_seed.py` [1.3], `local/webui/tests/test_catalog_store.py` [1.3], `local/webui/tests/test_catalog_xposed.py` [1.3], `local/webui/tests/test_build_zip.py` [1.2], `local/webui/catalog-seed.json` [1.3], `local/webui/static/item.html` and `item.js` [2.2], `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/DetailScreen.kt` [1.5].

Delete (tests inside kept files):
- `test_core_db.py`: `test_old_schedule_rows_become_app_rows` [1.1].
- `test_core_startup.py`: the `seed_import` step in the expected boot order [1.2].
- `test_core_scheduler.py`: every app-kind target/option test [1.2].
- `test_build_service.py`: appcheck tests, old `needs_build` tests [1.2].
- `test_catalog_upstream.py`: `auto_update`, `pressed`, `item_update_jobs`, schedule fan-out, `post_check`/`list_upstreams` tests [1.3].
- `test_ui_static.py`: `test_index_source_controls`, `test_item_page_has_no_mockup_leftovers`, `test_item_js_keeps_colour_picker_and_version_labels`; `NAV` updated [2.1].
- `src/build/test-build-item.sh`: `runner_version_unresolved` [1.2].

Old tests to port, read from git (they may already be deleted in the tree): `git show 0d55fc3:local/webui/tests/test_catalog_store.py`, `git show 0d55fc3:local/webui/tests/test_catalog_xposed.py`.

Budget check: the deletions remove about 50 tests; the new files add about 40 small ones. The macOS gate must still report ≤ 90 s total and no test over 1 s.

## 4. Removed-symbol greps (contract item 6)

Run from the repo root (bash or zsh); each must print nothing (exit 1). `schema/` and `test_core_db.py` are excluded because migrations 003–010 and the migration fixture name old tables by design; grep 3 also excludes `test_projects.py` (its v4 migration fixture names the old tables), and grep 5 matches the whole word (`compressed` is not a hit). Both instrument fixes were made by the lead after the round-2 review found the false positives.

```bash
grep -rnE --exclude-dir=__pycache__ --exclude-dir=schema --exclude=test_core_db.py "seed_import|export_seed|seed_text|catalog-seed|export-seed|DAILY_SCHEDULE|SEED_TABLES" local/webui src scripts README.md "docs/Frequently Asked Questions.md"
grep -rnE --exclude-dir=__pycache__ --exclude-dir=schema --exclude=test_core_db.py "\bgrp\b|group_id|enabled_group_items|download_group|group_zip_files|/api/groups" local/webui android/app/src
grep -rnE --exclude-dir=__pycache__ --exclude-dir=schema --exclude=test_core_db.py --exclude=test_projects.py "source_id|cli_upstream_id|FROM source|JOIN source|_source_fields|xposed_source_rules|list_sources|post_source" local/webui src/build
grep -rnE --exclude-dir=__pycache__ --exclude-dir=schema --exclude=test_core_db.py "auto_update|autoUpdate|setSwitches" local/webui android/app/src
grep -nE "\bpressed\b" local/webui/*.py
grep -rnE --exclude-dir=__pycache__ --exclude-dir=schema --exclude=test_core_db.py "_app_jobs|item_update_jobs|post_item_update|appcheck|check_all|checkAll|list_upstreams" local/webui src/build android/app/src
grep -rnE --exclude-dir=__pycache__ "detect_version|version_unresolved" local/webui src/build
grep -rnE --exclude-dir=__pycache__ "/api/items|/api/upstreams|'/api/catalog'|\"/api/catalog\"|/api/builds['\"]" local/webui android/app/src
grep -rnE "catalogItems|data-theme|prefers-color-scheme|relume-theme" local/webui/static
grep -rnE "isSystemInDarkTheme|darkColorScheme|dynamicLightColorScheme|dynamicDarkColorScheme|DayNight|FluentTheme|object Icons|\bIc\(" android/app/src
```

## 5. Round-1 gate (lead's gate step) and live migration check

Run by the lead (or one gate command dispatched by the lead) on main after all round-1 tasks are signed off. Record each command with a self-captured `RC=$?` and `git rev-parse HEAD` in `docs/logs/2026-10-26-mp-round1-gate.md`.

1. macOS suite: `python3 scripts/test.py --legs python,shell > .claude/tmp/mp-gate/r1-mac.log 2>&1; echo RC=$?` → 0.
2. Linux gate (command in contract item 2) → 0. If the Docker build exceeds the foreground limit, the lead asks the user to run it (memory: the user runs long builds).
3. Android leg (contract item 3) → 0.
4. Live migration check. Task 1.1 writes and first runs it (its acceptance); the gate re-runs it on the merged HEAD:
   ```bash
   rm -rf .claude/tmp/mp-migrate/data && mkdir -p .claude/tmp/mp-migrate/data .claude/tmp/mp-migrate/key
   sqlite3 local/data/webui.db ".backup .claude/tmp/mp-migrate/data/webui.db"
   python3 .claude/tmp/mp-migrate/check.py .claude/tmp/mp-migrate/data > .claude/tmp/mp-migrate/check.log 2>&1; echo RC=$?
   ```
   `check.py` (scratch, by task 1.1) inserts `local/` into `sys.path`, sets `RELUME_KEY` to the scratch key dir, calls `db.init(<data>)`, `db.migrate()`, then asserts every value of spec §4 "Expected result" and prints one `ok <name>` line per assertion. `.backup` uses SQLite's online backup and only reads the live file; nothing else touches `local/data`.
5. Boot smoke on the migrated copy (contract item 8): `check.py --boot` additionally calls `webui.__main__.boot()` (no `start`) and serves the app with `tests/helpers.serve`-style setup on a free port, then makes the four requests of contract item 8 with a session from `auth` (scratch admin created on the copy only). The catalog refresh in this smoke is the only network call (official `bundles.json`); on network failure the smoke reports the 502 text and the lead decides.

Live deployment: after the round-2 sign-off only, by the user (spec §11).

## 6. Round-2 reviewer

One fresh opus member (`podium:team-reviewer` type, read-only plus Bash for gates). Starts when all round-2 tasks report `READY_FOR_SIGNOFF`; reviews every commit since `0d55fc3`.

Dimensions:
1. **Spec conformance**: every spec §2–§8 behaviour has code; every §12 resolution is applied (e.g. Enable without URL, `default_enabled`, `icon_url` always the route, no cron, custom sources on Android).
2. **Data integrity**: migration 010 order, explicit orphan deletes with foreign keys off, content-based matching (no fixed ids), `inputs_sha`, catalog apply keeps chosen bundles.
3. **Security**: runner isolation for every foreign page or binary (`fetch-page.sh` via `jobs.run_isolated`), HTML parsing only reads attributes, icon allowlist on request and final URL, size caps, no external URL in static files, auth on every new route, `SESSION_ONLY` unchanged, no GitHub token in catalog or APKMirror requests.
4. **Error handling**: no swallowed exceptions, no silent defaults, messages with object + expected + actual, catalog skip records visible in `feed_status()`.
5. **Removals**: plan §4 greps, plan §3 deletions, no dead code, no dark-mode code.
6. **Tests**: plan §3 table present; each test ≤ 1 s; no forwarding-only tests; no `sleep`.
7. **UI**: web follows `web/` mockups (Fluent 2 class names, SVG icon buttons with `aria-label`), Android follows `android/tokens.md` roles (fixed palette, one step smaller type, official Material Symbols drawables), Android has Install and no Download.

Acceptance checks the reviewer runs (with self-captured RC, logged in `docs/logs/2026-10-26-mp-round2-review.md`): contract items 1, 2, 3, 4, 5, 6, 7, 8 and 9 (item 9: `python3 .claude/tmp/mp-web/render.py .claude/tmp/mp-migrate/data / /apps.html /schedules.html /builds.html /devices.html /projects.html /settings.html` → RC=0, script in plan §8). Findings: blocker / should-fix / nit with `file:line`; at most two review passes per task; should-fix and nit go to the parking lot. Per user feedback, a reviewer "merge-ready" closes the round; no extra gate run follows.

## 7. Round 1 tasks

### Task 1.1: Migration 010 and shared test fixtures

**Goal:** One migration file turns any database built by 001–009 into the spec §4 shape; the shared test fixtures speak the new model; OTA and notify SQL stop joining `source`/`grp`; the live migration is verified on a backup copy.

**Read:** spec §0, §2.4, §4, §2.6 (FakeHTTP 4-tuple); apps part §2, §3, §7; catalog part §2 (DDL); API part §1.4 (`apkm_*` columns).

**Files:**
- Create: `local/webui/schema/010_marketplace.sql`
- Modify: `local/webui/db.py` (`SUBDIRS` + `'catalog'`), `local/webui/ota.py` (`assigned_items`, lines 21-33), `local/webui/notify.py` (`_label`, lines 69-72)
- Rewrite: `local/webui/tests/catalog_rows.py`, `local/webui/tests/device_helpers.py` (row seeding only)
- Modify (fixture rows only): `local/webui/tests/test_core_db.py`, `test_device_adb.py`, `test_device_app.py`, `test_device_mdns.py`, `test_device_notify.py`, `test_device_ota.py`, `test_device_otajob.py`, `test_device_routes.py`, `test_projects.py`
- Scratch (not committed): `.claude/tmp/mp-migrate/seed_rows.py` (generates the 41 `INSERT OR IGNORE INTO app` rows from `git show 0d55fc3:local/webui/catalog-seed.json`), `.claude/tmp/mp-migrate/check.py`; evidence log `docs/logs/2026-10-26-mp-migration-check.md` (command, `RC=$?`, HEAD, the `ok` lines).

**Interfaces:**
- Produces (schema, exact): catalog part §2 tables + `catalog_feed.skipped TEXT`; `item(id, package UNIQUE REFERENCES app, bundle_id NOT NULL REFERENCES bundle, display_name, enabled, version_mode, version_pin, allow_nearest, variants, patch_overrides, engine_options)` with the `version_pin` CHECK of apps part §2.1; `app` + `apkm_state NOT NULL DEFAULT 'pending'` (+ CHECK), `apkm_candidates`, `apkm_error`, `apkm_checked_at`, `apkm_retry_at`, minus `display_name`, `icon_file`, `icon_fetched_at`; `build_jobs.inputs_sha TEXT`; `schedules.kind IN ('update','ota')` + `CREATE UNIQUE INDEX schedules_one_update ON schedules(kind) WHERE kind = 'update'`; `jobs.kind IN ('build','upstream','ota')`; built-in upstream + bundle rows always present.
- Produces (`tests/catalog_rows.py`): `VARIANTS`; `builtin_id(c) -> int` (the bundle row 010 created); `add_bundle(c, repo, *, origin='official', engine='morphe', host='github', upstream_id=None, asset_regex=None, name=None) -> int`; `add_item(c, package, bundle_id, *, enabled=1, **cols) -> int` (inserts the `app` row with `INSERT OR IGNORE`); `add_store_app(c, package, name, category=None, icon_url=None)`; `add_basic(c) -> dict` = `{"cli": <morphe-desktop stable upstream id>, "builtin": id, "official": id, "items": [1, 2, 3]}` with items 1 YouTube (builtin, enabled), 2 Reddit (builtin, enabled), 3 YT Music (official, disabled); `FakeHTTP` returning `(status, headers, body, final_url)` (final URL = request URL unless the route gives a 4th element); `FakeLog`; `CatalogCase` (temp DB + `add_basic` + `settings.github_token` and `events.emit` patched; no `catalog.build` patch).
- Produces (`tests/device_helpers.py`): same public names as today; item rows via `catalog_rows.add_item`.
- Consumes: nothing from other round-1 tasks.

**Behavior:** SQL order and content as spec §4 items 1–6 and apps part §7 steps 1–9. Every match is by content (repo/channel/tag, kind, foreign keys), never by a fixed id. Skeleton (complete the column lists from the DDL above; keep the statement order):

```sql
PRAGMA foreign_keys=OFF;
BEGIN;
-- 1. catalog DDL (catalog part §2; catalog_feed gets "skipped TEXT")
CREATE TABLE bundle (...); CREATE UNIQUE INDEX bundle_key ON bundle(host, repo, ifnull(branch, ''));
CREATE TABLE bundle_app (...); CREATE INDEX bundle_app_pkg ON bundle_app(package);
CREATE TABLE bundle_patch (...); CREATE TABLE store_app (...); CREATE TABLE catalog_feed (...);
INSERT OR IGNORE INTO upstream(kind, repo, channel) VALUES ('patches', 'MorpheApp/morphe-patches', 'stable');
INSERT INTO bundle(origin, host, repo, engine, name, upstream_id)
  SELECT 'builtin', 'github', repo, 'morphe', 'Morphe Patches', id FROM upstream
  WHERE repo = 'MorpheApp/morphe-patches' AND channel = 'stable' AND tag IS NULL;
-- 2. app columns
ALTER TABLE app ADD COLUMN apkm_state TEXT NOT NULL DEFAULT 'pending'
  CHECK (apkm_state IN ('pending','found','manual','not_found','ambiguous','blocked','failed'));
ALTER TABLE app ADD COLUMN apkm_candidates TEXT;  -- + apkm_error, apkm_checked_at, apkm_retry_at
UPDATE app SET apkm_state = 'manual' WHERE apkmirror_list_url IS NOT NULL OR apkpure_url IS NOT NULL;
ALTER TABLE app DROP COLUMN display_name;          -- + icon_file, icon_fetched_at
-- 3. item rebuild, ids kept, lowest id per package on the built-in source (apps part §7 step 2)
CREATE TABLE item_new (...);
INSERT INTO item_new(...) SELECT ... FROM item i JOIN source s ON s.id = i.source_id
  JOIN bundle b ON b.upstream_id = s.upstream_id AND b.origin = 'builtin'
  WHERE i.id = (SELECT min(i2.id) FROM item i2 JOIN source s2 ON s2.id = i2.source_id
                WHERE s2.upstream_id = s.upstream_id AND i2.package = i.package);
-- 4. explicit orphan deletes of dropped items (FKs are off): item_dependency, device_assignments,
--    ota_installs (kind 'item'), artifacts, build_jobs, jobs of kind build whose target item is gone
DELETE FROM item_dependency WHERE item_id NOT IN (SELECT id FROM item_new);  -- and the others in this order
DROP TABLE item; ALTER TABLE item_new RENAME TO item; DROP TABLE source; DROP TABLE grp;
ALTER TABLE build_jobs ADD COLUMN inputs_sha TEXT;
-- 5. schedules rebuild: kind IN ('update','ota'); one update row from the lowest-id app row with options.update
--    (id, name, spec, enabled, last_run_at, next_run_at kept; target '{}'; options {"notify": <old notify>});
--    jobs.schedule_id of dropped rows -> NULL; CREATE UNIQUE INDEX schedules_one_update ...
-- 6. jobs: DELETE kind 'appcheck'; rebuild with kind IN ('build','upstream','ota') (pattern 006:6-21)
-- 7. orphan upstream rows + their bundle_info; bundle_info of tags other than downloaded_tag
DELETE FROM upstream WHERE id NOT IN (SELECT upstream_id FROM bundle WHERE upstream_id IS NOT NULL)
  AND id NOT IN (SELECT upstream_id FROM project)
  AND NOT (repo = 'MorpheApp/morphe-desktop' AND channel = 'stable' AND tag IS NULL)
  AND NOT (repo = 'JingMatrix/LSPatch' AND channel = 'stable' AND tag IS NULL)
  AND NOT (repo = '7723mod/NPatch' AND channel = 'tag' AND tag = 'v1.0.5');
DELETE FROM bundle_info WHERE upstream_id NOT IN (SELECT id FROM upstream);
DELETE FROM bundle_info WHERE tag IS NOT (SELECT downloaded_tag FROM upstream u WHERE u.id = bundle_info.upstream_id);
-- 8. seed URL rows (generated by .claude/tmp/mp-migrate/seed_rows.py, 41 rows)
INSERT OR IGNORE INTO app(package, apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url, apkm_state)
  VALUES (...,'manual'), ...;
COMMIT;
PRAGMA foreign_keys=ON;
```

`ota.assigned_items` label = `COALESCE(item.display_name, store_app.name, item.package)` (LEFT JOIN `store_app`), order `ORDER BY item.id`. `notify._label` = `COALESCE(item.display_name, store_app.name, item.package) || ' · ' || bundle.name` (JOIN `bundle`, LEFT JOIN `store_app`).

**Constraints:** no Python migration hook (`db.migrate` runs SQL only); SQLite ≥ 3.35 (`DROP COLUMN`; Debian trixie and macOS have it); the live DB is read only through `sqlite3 local/data/webui.db ".backup <scratch>"`; never write to `local/data`.

**Tests (commit):** in `test_core_db.py`, replace `AppScheduleMigrationTest.test_old_schedule_rows_become_app_rows` with `MarketplaceMigrationTest`:
- `test_migration_010_keeps_builtin_items_and_drops_the_rest`: migrate a temp DB with files `00[1-9]_*.sql` only (copy pattern of the old test), insert: upstreams CLI + built-in stable + other patches + an orphan; sources `morphe` (built-in) and `other`; `grp`; items 1 (built-in, enabled, pin + overrides), 2 (built-in, disabled), 3 (built-in, dependency + device assignment), 4 (other source, with artifact, build_job, assignment, `ota_installs` row, build job row); an `app` schedule with `options.update = true`; an `appcheck` job. Then run `010` and assert: item ids {1, 2, 3} with `bundle_id` = built-in and their columns; item 4 and all its rows gone; `PRAGMA foreign_key_check` empty; one `update` schedule with the old id/spec/next_run; no `appcheck` job; orphan upstream gone, CLI kept; `apkm_state` of URL rows = `manual`; one seed row present (`com.reddit.frontpage` list URL).
- `test_migration_010_fresh_db_has_builtin_bundle`: full migrate on an empty DB → one `bundle` row `origin='builtin'` whose upstream is `MorpheApp/morphe-patches` stable; 41 `app` rows; zero `schedules` rows.

**Acceptance:**
- [ ] `cd local && python3 -m unittest webui.tests.test_core_db > ../.claude/tmp/mp-1.1/t.log 2>&1; echo RC=$?` → `RC=0`, both new test names in `t.log` (run with `-v`).
- [ ] `grep -n "test_old_schedule_rows_become_app_rows" local/webui/tests/test_core_db.py` → no hit.
- [ ] `grep -nE "JOIN source|grp" local/webui/ota.py local/webui/notify.py` → no hit.
- [ ] Device and project tests compile against the new fixtures: `cd local && python3 -m unittest webui.tests.test_device_ota webui.tests.test_device_otajob webui.tests.test_device_notify webui.tests.test_device_routes webui.tests.test_device_app webui.tests.test_device_adb webui.tests.test_device_mdns webui.tests.test_projects -v > ../.claude/tmp/mp-1.1/d.log 2>&1; echo RC=$?` → `RC=0` once tasks 1.2/1.3 have committed the functions those tests call; before that, failures are allowed only when the traceback ends in a peer's file (list them in the report).
- [ ] Live migration check (plan §5 step 4) → `RC=0`, every spec §4 expected value printed as `ok …`, log in `docs/logs/2026-10-26-mp-migration-check.md`.

**Targeted command:** the two unittest commands above (each < 30 s).

**Steps:**
- [ ] Write `MarketplaceMigrationTest` (both tests) against the schema above; run → fails (no `010` file).
- [ ] Write `seed_rows.py` (scratch), generate the 41 rows, write `010_marketplace.sql`; run the test → green.
- [ ] Rewrite `catalog_rows.py` and `device_helpers.py`; add `'catalog'` to `db.SUBDIRS`; commit **first commit** (`010`, `db.py`, `catalog_rows.py`, `device_helpers.py`, `test_core_db.py`) and send the hash to the lead.
- [ ] Re-point `ota.assigned_items` and `notify._label`; update device/project test fixtures; run the device command.
- [ ] Write `check.py`, run the live migration check, write the evidence log; commit the rest.

### Task 1.2: Enabled-app model, build flow and global schedule

**Goal:** New `apps.py` owns enabled apps, their settings, build inputs, the rebuild fingerprint and the update run; `build.py`, `scheduler.py`, `jobs.py`, `__main__.py` and the build shell scripts drop the item/source/group/switch/appcheck code.

**Read:** spec §1, §3, §4 (result shape), §6.2, §6.3; apps part §1–§6, §8; API part §2.2 (app object, status values), §4 (error codes); code: `catalog.py:585-737` at `0d55fc3` (patches, version, `job_input` — moved here), `build.py`, `scheduler.py`, `upstream.py:423-505`, `src/build/build-item.sh:20-140`, `src/build/utils.sh:45-57,160-175,420-430`.

**Files:**
- Create: `local/webui/apps.py`, `local/webui/tests/test_apps.py`
- Modify: `local/webui/build.py`, `local/webui/scheduler.py`, `local/webui/jobs.py` (`OPTION_DEFAULTS` only), `local/webui/__main__.py`, `src/build/build-item.sh`, `src/build/utils.sh`, `src/build/test-build-item.sh` (+ `src/build/test-fixtures/*` it reads), `local/webui/tests/test_build_service.py`, `test_core_scheduler.py`, `test_core_startup.py`, `test_core_jobs.py` (only if it references `appcheck`/`rebuild` defaults), `local/webui/tests/fixtures/build/check-*.sh` (keep only if `check_versions` tests use them)
- Delete: `local/webui/tests/test_build_zip.py`

**Interfaces:**
- Consumes (task 1.1): schema of spec §4; `tests/catalog_rows.py` helpers.
- Consumes (task 1.3, mock until committed): `catalog.ensure_upstream(bundle_id) -> int`; `catalog.engine_cli(engine) -> int`; `catalog.patch_list(bundle_id, package) -> (tag | None, files: list[str], patches: list[dict] | None)`; `catalog.app_bundles(package) -> list[dict]` (spec §2.5); `catalog.refresh(log)` raising `catalog.CatalogRefreshFailed` / `catalog.CatalogRefreshBusy`; `catalog.validate_variants`, `default_variants`, `validate_engine_options`, `check_xposed_variants`, `is_module`, `version_key`, `UpstreamMissing`; `upstream.pull(upstream_id, log, job_id=None) -> bool`.
- Consumes (task 1.4, mock until committed): `apklocate.locate(package, log, cancel_event) -> None`; `apklocate.throttle(cancel_event) -> None`; `server.HttpError(status, message, code=None, **extra)`.
- Produces (`apps.py`, exact):
  ```python
  KEYSTORE = {'morphe': 'morphe.keystore', 'lspatch': 'lspatch-signing.p12', 'npatch': 'fiorenmas.ks'}
  STATES = ('up_to_date', 'update_available', 'building', 'queued', 'failed', 'not_built', 'needs_download_url')
  def list_enabled() -> list[dict]            # app objects of API part §2.2 without "download", enabled = 1 only, ORDER BY id
  def get(app_id: int) -> dict                # app object + "settings" {version{mode,pin,allow_nearest,supported,apkmirror}, project_ids, variants, engine_options, display_name, stale_overrides}; HttpError 404
  def enable(package: str, bundle_id: int, overrides: dict[str, bool] | None = None) -> dict   # spec §3.4; enqueues upstream {item_id}; 404 / 409 bundle_unsupported / bundle_not_selectable / already_enabled; 400 patch_unknown
  def disable(app_id: int) -> None            # enabled = 0, nothing else
  def update_settings(app_id: int, fields: dict) -> dict   # keys bundle_id, version, project_ids, variants, engine_options, display_name; unknown key -> 400; returns get(app_id)
  def get_patches(app_id: int) -> dict        # today's get_patches shape; 409 for module bundles
  def put_patches(app_id: int, body: dict) -> dict
  def check_update(app_id: int) -> int        # job id of upstream {item_id}, trigger manual; 409 app_disabled
  def rebuild(app_id: int) -> int             # job id of build {item_id}; 409 app_disabled, 409 needs_download_url
  def get_schedule() -> dict                  # {id, spec, enabled, options: {notify}, last_run_at, next_run_at}
  def put_schedule(fields: dict) -> dict      # keys spec, enabled, options.notify; spec via scheduler._check_spec
  def run_schedule() -> int                   # enqueue upstream {all: True}, trigger manual, schedule_id = the update row
  def run_update(job, log) -> None            # apps part §5 steps 1-7 with spec §3.2
  def check_versions(package: str, log, cancel_event) -> None   # body of build.appcheck_handler; apklocate.throttle first
  def want_version(item_id: int) -> str | None   # pinned -> pin; auto -> max by version_key of compatible_versions at the downloaded tag, else as latest; latest -> app.apkmirror_versions[0] or None
  def job_input(item_id: int) -> dict         # apps part §4.1; version = {"value": None if mode latest or auto-empty else want_version, "near": bool}
  def inputs_sha(item_id: int) -> str         # sha256 of jobs.canon({bundle_id, patches_tag, cli_tag, version, overrides, engine_options, variants})
  def stale_overrides(item_id: int) -> list[str]
  ```
- Produces (`build.py`): `handler` uses `apps.job_input`/`apps.stale_overrides` and writes `build_jobs.inputs_sha`; `item_status(item_ids)` (unchanged shape); `needs_build(item_id) -> bool` (apps part §4.3); `list_artifacts(item_id) -> list[dict]` (today's `list_item_artifacts` body without the request); `artifact_name(package, variant, app_version, upstream_tag)`; routes kept: `GET /api/artifacts/{id}/download`, `GET|POST /api/cleanup`; `_cleanup_plan` adds orphan `artifacts/<id>` and `upstream/<id>` dirs.
- Produces (`scheduler.py`): kinds `update`/`ota`; `ensure_update()` (creates `('Update', 'update', {}, {'every': 'day', 'at': '09:00'}, {'notify': True})` when no row exists); `fire` for `update` → `jobs.enqueue('upstream', {'all': True}, 'schedule', sid, {'notify': o['notify']})`; `/api/schedules*` routes list and accept kind `ota` only.
- Produces (`__main__.py`): `boot()` without `seed_import`, calls `scheduler.ensure_update()` after `register()`; no `export-seed` command; `USAGE = 'usage: python3 -m webui [serve]'`.

**Behavior:** apps part §4–§6 with spec §3. Status mapping in `list_enabled`/`get`: active queued job → `queued`; running → `building` (with `job_id`, `progress`); no usable URL for a variant's download kind → `needs_download_url`; last finished build failed → `failed` (+ `fail_code`, `fail_detail`); never built → `not_built`; `build.needs_build` → `update_available`; else `up_to_date`. `run_update` catches `CatalogRefreshFailed`/`CatalogRefreshBusy` and continues; per-upstream failures continue; `TokenRejected`/`RateLimited` stop the GitHub step; locate and version check run per app in id order; the job raises `jobs.JobFailed("<n> steps failed: catalog: …; upstream 7: …")` at the end when any step failed, after enqueuing builds.

**Remove (with tests):** apps part §6 rows Groups (build.py parts), Build/Update switches, Multi-target schedules, Version in shell; `build.appcheck_handler` and its registration; `post_item_build`, `post_builds`, `list_item_artifacts` route, `group_zip_files`, `_slug`, `download_group` and the `zipfile`/`time` imports if unused; `version_unresolved` from `FAIL_CODES`; `jobs.OPTION_DEFAULTS['appcheck']`, `upstream.rebuild` default.

**Tests (commit):** plan §3 rows for `test_apps.py`, `test_core_scheduler.py`, `test_build_service.py`, `test-build-item.sh`. Port the patch/version/module tests from `git show 0d55fc3:local/webui/tests/test_catalog_store.py` and `…/test_catalog_xposed.py` to `apps.get_patches`/`put_patches`/`update_settings` (same assertions, new row setup). Fakes: `mock.patch.object(catalog, 'refresh', …)`, `mock.patch.object(upstream, 'pull', …)`, `mock.patch.object(apklocate, 'locate', …)`.

**Acceptance:**
- [ ] `cd local && python3 -m unittest webui.tests.test_apps webui.tests.test_build_service webui.tests.test_core_scheduler webui.tests.test_core_startup webui.tests.test_core_jobs -v > ../.claude/tmp/mp-1.2/t.log 2>&1; echo RC=$?` → `RC=0`; every plan §3 method name of these files appears in `t.log`.
- [ ] `bash src/build/test-build-item.sh > .claude/tmp/mp-1.2/sh.log 2>&1; echo RC=$?` → `RC=0`; `grep -c "runner_version_unresolved" .claude/tmp/mp-1.2/sh.log` → `0`.
- [ ] `grep -rnE "detect_version|version_unresolved" src/build local/webui --exclude-dir=__pycache__` → no hit.
- [ ] `grep -nE "auto_update|pressed|appcheck|group_zip|download_group|_app_jobs|seed_import|export-seed|source_id" local/webui/apps.py local/webui/build.py local/webui/scheduler.py local/webui/jobs.py local/webui/__main__.py` → no hit.
- [ ] `ls local/webui/tests/test_build_zip.py` → "No such file".

**Targeted command:** the unittest command above (< 30 s) and the shell script (< 30 s).

**Steps:**
- [ ] **First commit** (send hash to the lead): `__main__.py` without `seed_import`/`export-seed`, `scheduler.ensure_update()` + `test_ensure_update_creates_missing_row`, `test_core_startup.py` boot order.
- [ ] Write `test_apps.py` cases for `want_version`, `inputs_sha`/`needs_build`, enable/re-enable, bundle change, unknown patch; run → fail (no `apps`).
- [ ] Write `apps.py` (move the patch/version/engine-option logic from `catalog.py` at `0d55fc3` unchanged in logic); run → green.
- [ ] Rewrite `build.py` parts (handler, `needs_build`, `artifact_name`, `list_artifacts`, cleanup plan, removals); update `test_build_service.py`.
- [ ] Rewrite `scheduler.py` kinds and fire; update `test_core_scheduler.py`; `jobs.OPTION_DEFAULTS`.
- [ ] `run_update` + its two tests; shell changes + `test-build-item.sh`; run all targeted commands; commit.

### Task 1.3: Catalog layer, upstream trimming, seed removal

**Goal:** `catalog.py` becomes the catalog layer of spec §2 (feeds, parsers, apply, built-in and custom sources, patch-list lookup, icons); `upstream.py` loses the source/item fan-out and dispatches update runs to `apps.run_update`; Follow projects get their own pull route; the seed file and seed code are gone.

**Read:** spec §1, §2 (all), §6.4–§6.6; catalog part §0–§7 and "Interfaces"; icon check §6 G1–G4; code: `catalog.py`, `upstream.py`, `projects.py:360-500`, `settings.py:80-95`; sample feed `.claude/tmp/morphe-site/bundles.json` and scratch prototype `.claude/tmp/be-catalog/proto.py` (read only).

**Files:**
- Rewrite: `local/webui/catalog.py`
- Modify: `local/webui/upstream.py`, `local/webui/projects.py` (new `post_pull` route only), `local/webui/settings.py` (`http_get` 4-tuple at line 86)
- Create: `local/webui/tests/test_catalog_feed.py`, `local/webui/tests/fixtures/catalog/bundles-mini.json` (~40 lines, hand-made: both patch key pairs, three compatibility shapes, a target without version, `targets: null`, a universal patch, a GitLab bundle, a `branch: dev` bundle, a duplicate repo, one malformed bundle, one malformed store value), `local/webui/tests/fixtures/catalog/patches-list-mini.json`
- Modify: `local/webui/tests/test_catalog_upstream.py`, `local/webui/tests/test_catalog_inspect.py` (imports only), `local/webui/tests/test_core_settings.py` (only if it fakes `http_get`)
- Delete: `local/webui/catalog-seed.json`, `local/webui/tests/test_catalog_seed.py`, `local/webui/tests/test_catalog_store.py`, `local/webui/tests/test_catalog_xposed.py`

**Interfaces:**
- Consumes (task 1.1): schema; `tests/catalog_rows.py` (`CatalogCase`, `FakeHTTP` 4-tuple, `add_bundle`, `add_item`, `add_store_app`).
- Consumes (task 1.2, lazy import inside the handler, mock in tests): `apps.run_update(job, log) -> None`.
- Consumes (task 1.4): `server.HttpError(status, message, code=None, **extra)`.
- Produces (`catalog.py`, exact names; shapes in spec §2.5 and catalog part "Interfaces"):
  ```python
  UNIVERSAL = '*'; FEEDS = (("official", "https://morphe-patches.software/data/bundles.json", parse_official),)
  FEED_RANK = {"builtin": 0, "custom": 1, "official": 2}
  ICON_HOSTS: frozenset[str]; ICON_MAX_BYTES = 256 * 1024; PLAY_PAGE_MAX_BYTES = 4 * 1024 * 1024
  FEED_MAX_BYTES = 16 * 1024 * 1024; PLAY_ICON_SIZE = "=s256-rw"
  GITLAB_REASON = "GitLab releases not supported"
  ENGINE_CLI = {"morphe": ("MorpheApp/morphe-desktop", "stable", None, r"^morphe-desktop-.*-all\.jar$"),
                "lspatch": ("JingMatrix/LSPatch", "stable", None, r"^lspatch-.*-release\.jar$"),
                "npatch": ("7723mod/NPatch", "tag", "v1.0.5", r"^jar-.*-release\.jar$")}
  class CatalogInvalid(ValueError); class CatalogRefreshFailed(Exception); class CatalogRefreshBusy(Exception)
  @dataclass(frozen=True) Patch, Bundle, StoreApp, Skip, Catalog(bundles, store, duplicates, skipped)
  def parse_official(raw: bytes) -> Catalog
  def parse_patches_list(raw: bytes) -> tuple[str, list[dict], dict[str, list[str]], dict[str, str], list[Skip]]  # version, patches, apps->versions, package->name, skipped
  def refresh(log) -> None                        # holds _refresh_lock; raises CatalogRefreshBusy / CatalogRefreshFailed after applying what succeeded
  def feed_status() -> dict                       # spec §2.5
  def market_apps() -> list[dict]                 # spec §2.5
  def app_bundles(package: str) -> list[dict]     # spec §2.5; KeyError for an unknown package
  def ensure_upstream(bundle_id: int) -> int      # HttpError(409, ..., code='bundle_not_selectable') for GitLab
  def engine_cli(engine: str) -> int              # upstream_row for ENGINE_CLI[engine]
  def patch_list(bundle_id: int, package: str) -> tuple[str | None, list[str], list[dict] | None]
  def bundle_versions(bundle_id: int, package: str) -> list[str]
  def list_custom() -> list[dict]; def add_custom(url, engine, asset_regex=None, packages=()) -> int; def delete_custom(bundle_id: int) -> None
  # kept: ABIS, display_name, version_key, upstream_row, set_apkmirror_versions, UpstreamMissing, default_variants,
  #       validate_variants, validate_engine_options, check_xposed_variants, is_module, get_icon, post_icon_refresh
  ROUTES = [("GET", "/api/apps/{pkg}/icon", get_icon), ("POST", "/api/apps/{pkg}/icon/refresh", post_icon_refresh)]
  ```
- Produces (`upstream.py`): `http_get(url, headers=None, timeout=30, max_bytes=None) -> (status, headers, body, final_url)`; `pull(upstream_id, log, job_id=None) -> bool` (True = a new tag was downloaded); `affected_items(upstream_id) -> list[int]` (enabled items whose bundle uses the row, or whose bundle engine uses it as CLI via `engine_cli`, plus items depending on a project with that row); `_packages(upstream_id)` per catalog part "Interfaces"; `_prune` also deletes `bundle_info` rows of pruned tags; job kind `upstream` handler: target `{upstream_id}` → `pull`; `{all: True}` or `{item_id}` → `apps.run_update(job, log)`; no routes left in `upstream.py`.
- Produces (`projects.py`): `POST /api/projects/{id}/pull` → `jobs.enqueue('upstream', {'upstream_id': <project upstream>}, 'manual')` → `{"job_id"}`; 404 for an unknown project.

**Behavior:** catalog part §1–§6 with spec §2.1–§2.8. The live `bundles.json` copy must parse with skips only, not a rejection (scratch check below). The icon route: on a miss, fetch from `store_app.icon_url`, else the Play page `og:image` (suffix rewritten to `=s256-rw`), host checked on request and `final_url`, caps per spec §2.6, failure → log line + `icon_tried_url` + 404.

**Remove (with tests):** catalog part §6 "Deleted" list for `catalog.py` and `upstream.py` (groups, sources, items, seed, icon loop, Play title scrape, `list_upstreams`, `post_check`, `check_all`, `_all_rows`, `post_pull`, `item_update_jobs`, `post_item_update`, `pressed`); `catalog.get_catalog` and every `/api/catalog`, `/api/groups`, `/api/sources`, `/api/items`, `/api/apps/{pkg}` (PUT), `/api/upstreams*` route. Delete the seed code only after task 1.2's first commit (boot no longer calls it).

**Tests (commit):** plan §3 rows for `test_catalog_feed.py` and `test_catalog_upstream.py`. No test touches the network; HTTP via `FakeHTTP`.

**Scratch check (not committed):** `python3 -I .claude/tmp/mp-1.3/live_parse.py .claude/tmp/morphe-site/bundles.json` (script adds `local/` to `sys.path` explicitly) prints bundle count (expect ≥ 290), package count, skipped list, duplicates; RC=0.

**Acceptance:**
- [ ] `cd local && python3 -m unittest webui.tests.test_catalog_feed webui.tests.test_catalog_upstream webui.tests.test_catalog_inspect webui.tests.test_core_settings -v > ../.claude/tmp/mp-1.3/t.log 2>&1; echo RC=$?` → `RC=0`; every plan §3 method name of these files appears in `t.log`.
- [ ] `grep -nE "seed_import|export_seed|seed_text|SEED_|DAILY_SCHEDULE|grp|FROM source|JOIN source|list_sources|post_source|item_update_jobs|post_item_update|list_upstreams|post_check|check_all|_icon_loop|xposed_source_rules|check_version_mode|get_catalog" local/webui/catalog.py local/webui/upstream.py` → no hit.
- [ ] `ls local/webui/catalog-seed.json local/webui/tests/test_catalog_seed.py local/webui/tests/test_catalog_store.py local/webui/tests/test_catalog_xposed.py` → 4 × "No such file".
- [ ] `grep -n "'/api/" local/webui/upstream.py` → no hit; `grep -n "/api/projects/{id}/pull" local/webui/projects.py` → 1 hit.
- [ ] Scratch live parse RC=0 with bundle count ≥ 290 (state the numbers in the report).

**Targeted command:** the unittest command above (< 30 s).

**Steps:**
- [ ] **First commit** (send hash to the lead): `http_get` 4-tuple + `max_bytes` with every caller (`upstream.fetch`, `settings.py`, icon fetch) + `test_http_get_returns_final_url_and_caps_body`; `ensure_upstream`, `engine_cli`, `patch_list`, `bundle_versions`.
- [ ] Write `bundles-mini.json`, `patches-list-mini.json` and the parser tests; run → fail; write `parse_official`, `parse_patches_list` → green.
- [ ] Refresh + apply + `feed_status` + `market_apps` + `app_bundles` with their tests.
- [ ] Custom sources, icons with their tests.
- [ ] Trim `upstream.py` (handler dispatch, `affected_items`, `_packages`, `_prune`), `projects.py` pull route; remove the old code and test files; run the greps; commit.

### Task 1.4: APKMirror locator and HTTP API

**Goal:** `apklocate.py` finds the APKMirror listing of an enabled app by package with an exact Play-id match, rate-limited and visible per app; `market.py` serves every new route of spec §6 on top of `apps`, `catalog`, `apklocate` and `build`.

**Read:** spec §1, §5, §6, §12 items 5, 6, 8, 9, 19, 22; API part §1–§5; code: `build.appcheck_handler` (`build.py:165-198` at `0d55fc3`, isolation pattern), `jobs.run_isolated`/`open_runner_file` (`jobs.py:104-140`), `server.py` (router, `HttpError`, envelope at `:170`), `auth.py:17-21`, `src/build/utils.sh:59-131` (`_cf_get`); saved pages `.claude/tmp/research-official/a1.html`, `.claude/tmp/be-api/app-youtube.html`, `.claude/tmp/be-api/app-ogyt.html`.

**Files:**
- Create: `local/webui/apklocate.py`, `local/webui/market.py`, `src/build/fetch-page.sh`, `local/webui/tests/test_apklocate.py`, `local/webui/tests/test_market_api.py`, `local/webui/tests/fixtures/apkmirror/search-youtube.html`, `app-youtube.html`, `app-ogyoutube.html` (trimmed to the needed rows, a few KB each), `challenge.html`, `search-empty.html` (hand-written)
- Modify: `local/webui/server.py` (`HttpError` code/extra fields + envelope; `MODULES` += `market`), `local/webui/tests/test_core_router.py` (only if it asserts `MODULES` or the envelope)

**Interfaces:**
- Consumes (task 1.1): schema (`app.apkm_*`, `store_app.name`).
- Consumes (task 1.2, mock until committed): every `apps.*` function in task 1.2 "Produces"; `build.list_artifacts(item_id)`; `build.SOLVER_ENV`.
- Consumes (task 1.3, mock until committed): `catalog.market_apps`, `app_bundles`, `feed_status`, `refresh`, `CatalogRefreshBusy`, `CatalogRefreshFailed`, `list_custom`, `add_custom`, `delete_custom`.
- Produces (`server.py`, **first commit**, send hash to the lead): `class HttpError(Exception): def __init__(self, status, message, code=None, **extra)`; error body `{"error": message}` plus `"code"` and the extra keys when given.
- Produces (`apklocate.py`): spec §5.2 exactly (constants, `Cand`, `Result`, `Blocked`, `PageShape`, `search_url`, `app_url`, `list_url`, `parse_search`, `play_id`, `decide`, `throttle`, `locate`, `validate_list_url`, `put_download`, `download_state`, `reset_lookup`).
- Produces (`market.py`): `ROUTES` with exactly these entries, handlers per spec §6:
  `GET /api/marketplace`, `GET /api/marketplace/{pkg}`, `GET /api/apps`, `POST /api/apps`, `GET /api/apps/{id}`, `PUT /api/apps/{id}`, `DELETE /api/apps/{id}`, `GET /api/apps/{id}/patches`, `PUT /api/apps/{id}/patches`, `POST /api/apps/{id}/check`, `POST /api/apps/{id}/build`, `POST /api/apps/{id}/locate`, `GET /api/apps/{id}/artifacts`, `GET /api/schedule`, `PUT /api/schedule`, `POST /api/schedule/run`, `GET /api/catalog/status`, `POST /api/catalog/refresh`, `GET /api/sources`, `POST /api/sources`, `DELETE /api/sources/{id}`.
  `{id}` must be an integer (`server.is_int`), `{pkg}` a package (`server.validate_package`); the icon routes stay in `catalog.py`.
- Produces (`src/build/fetch-page.sh <url> <out>`): sources `utils.sh`, calls `_cf_get "$1"`, writes the HTML to `<out>`, exit 1 when the solver chain fails.

**Behavior:** API part §1 with spec §5 (ambiguous → user picks; 10 s spacing; locate due rules; manual retry resets to `pending`); API part §2–§4 with spec §6 (sort `stars`, `feed` in the list response, `POST /api/apps` accepts `download`, `PUT` triggers `check_update` when a URL was newly set, custom sources open to device tokens). `throttle` uses `_clock` and `cancel_event.wait(remaining)` (time is business data here: rate limit), raises `Blocked` during the cooldown. Parsing uses `html.parser` on text written by the isolated runner; nothing from the page runs.

**Tests (commit):** plan §3 rows for `test_apklocate.py` and `test_market_api.py`. `test_market_api.py` serves the app with `tests/helpers.serve(self, names=('market',))` and fakes `catalog`/`apps` functions with `mock.patch.object` where the logic under test is the route's own (filter/sort/paging, status codes, auth); `test_device_token_reaches_new_routes` issues a device token like `test_core_auth.py` does and calls `GET /api/marketplace`, `GET /api/apps`, `POST /api/sources` (fake), and asserts `PUT /api/settings` still answers 403 for that token.

**Acceptance:**
- [ ] `cd local && python3 -m unittest webui.tests.test_apklocate webui.tests.test_market_api webui.tests.test_core_router -v > ../.claude/tmp/mp-1.4/t.log 2>&1; echo RC=$?` → `RC=0`; every plan §3 method name of these files appears in `t.log`.
- [ ] `bash -n src/build/fetch-page.sh; echo RC=$?` → `RC=0`; `grep -n "_cf_get" src/build/fetch-page.sh` → 1 hit.
- [ ] Each of the 21 routes above is registered once: `grep -cF '("<METHOD>", "<path>",' local/webui/market.py` → 1 for every pair (the route table style of `build.py:459`).
- [ ] `grep -nE "urlopen|http_get" local/webui/apklocate.py` → no hit (all APKMirror traffic goes through the isolated runner).
- [ ] `grep -n "market" local/webui/server.py` → the `MODULES` line.

**Targeted command:** the unittest command above (< 30 s).

**Steps:**
- [ ] **First commit**: `HttpError` code/extra + envelope (+ router test if needed).
- [ ] Trim the saved pages into fixtures; write parser + `decide` + limiter tests; run → fail; write `apklocate.py` parts → green.
- [ ] `fetch-page.sh`, `locate` (isolated fetch, state writes), `put_download`, `download_state`, `reset_lookup`, `validate_list_url` + test.
- [ ] `market.py` routes + `test_market_api.py`; add `market` to `MODULES`; run; commit.

### Task 1.5: Android foundation (theme, components, icons, API client, navigation)

**Goal:** The Android app builds on the MD3 fixed light palette with official Material Symbols, talks to the new API (spec §6), and has the three-tab navigation with the avatar-opened Settings; kept screens (Login, Settings, Install, Log) are ported to the new components; Marketplace, Apps and Schedules exist in a thin first version that round 2 completes.

**Read:** spec §6, §8; `docs/superpowers/mockups/marketplace/candidate-2/android/tokens.md` (all), `android/m3.css` (role names), `android/icons-material.js` (path data), `android/*.html` (layouts); API part §2 (JSON shapes); code: everything under `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/` and `api/`, `res/values/themes.xml`; `docs/logs/2026-10-26-explore-frontend.md` §3.
Before writing any Compose API call, confirm it in the Compose Material 3 version resolved by BOM 2026.09.00 (`android/app/build.gradle.kts:95-106`) with context7 `query-docs` (e.g. `NavigationBar`, `FilterChip`, `SearchBar`, `HorizontalMultiBrowseCarousel`, `ModalBottomSheet`, `TooltipBox`, `lightColorScheme`). Do not add dependencies.

**Files (under `android/app/src/main/`; `K` = `kotlin/io/github/jhangyu/relume/`):**
- Rewrite: `K/ui/Theme.kt`, `K/ui/Components.kt`, `K/ui/Screen.kt`, `K/ui/RelumeRoot.kt`, `K/api/Models.kt`, `K/api/RelumeApi.kt`, `res/values/themes.xml`
- Create: `res/drawable/ms_<name>.xml` for every icon in `android/tokens.md` §6 (24 names; FILL 1 variants as `ms_<name>_fill1.xml`), `K/ui/MarketModel.kt`, `K/ui/MarketplaceScreen.kt`, `K/ui/MarketAppScreen.kt`, `K/ui/AppSettingsScreen.kt`, `K/ui/SchedulesScreen.kt`, `android/app/src/test/kotlin/io/github/jhangyu/relume/api/MarketParseTest.kt`
- Modify (port to the new components and API, no behaviour change unless listed): `K/ui/AppModel.kt`, `K/ui/AppsScreen.kt`, `K/ui/AppStatus.kt`, `K/ui/JobScreens.kt` (keep `LogScreen`, delete `BuildsScreen`), `K/ui/SettingsScreen.kt`, `K/ui/InstallScreen.kt`, `K/ui/LoginScreen.kt`, `K/ui/MainActivity.kt`, `K/sync/CheckWorker.kt` (only if it calls removed API functions), `android/app/src/test/kotlin/io/github/jhangyu/relume/api/PlanParseTest.kt` (only if `Models.kt` renames break it)
- Delete: `K/ui/DetailScreen.kt`

**Interfaces:**
- Consumes: spec §6 JSON shapes (server code from round 1 may still be in progress; code against the spec, not the server).
- Produces (frozen for round 2; round-2 tasks keep these signatures):
  ```kotlin
  // Screen.kt
  sealed interface Screen { data object Marketplace; data class MarketApp(val pkg: String); data object Apps;
    data class AppSettings(val appId: Int); data object Schedules; data object Settings;
    data class Log(val jobId: Int, val appId: Int?); data object Install }          // all @Serializable
  val Screen.isRoot: Boolean   // Marketplace, Apps, Schedules
  // RelumeRoot.kt wires exactly these composables:
  @Composable fun MarketplaceScreen(model: MarketModel, onOpen: (String) -> Unit, onAddSource: () -> Unit)
  @Composable fun MarketAppScreen(model: MarketModel, pkg: String, onBack: () -> Unit, onEnabled: (Int) -> Unit)
  @Composable fun AppsScreen(model: AppModel, onOpenSettings: (Int) -> Unit, onOpenLog: (Int, Int?) -> Unit)
  @Composable fun AppSettingsScreen(model: AppModel, appId: Int, onBack: () -> Unit, onOpenLog: (Int, Int?) -> Unit)
  @Composable fun SchedulesScreen(model: AppModel)
  @Composable fun SettingsScreen(model: AppModel, notificationsOn: Boolean, askNotifications: () -> Unit, onBack: () -> Unit)
  // Theme.kt
  val RelumeLight: ColorScheme; @Immutable data class RelumeColors(...)  // tokens.md §3 pastel pairs + extended roles
  val LocalRelumeColors: ProvidableCompositionLocal<RelumeColors>; @Composable fun RelumeTheme(content: @Composable () -> Unit)
  // Components.kt (MD3-based): AppTile(label, iconUrl, api, size), StatusPill(state), OriginLabel(origin), CategoryTag(category),
  //   IconAction(icon: Int /* R.drawable.ms_* */, contentDescription, onClick, tone), InsetGroup { }, SectionHeader(text)
  ```
  `api/Models.kt` (kotlinx-serialization, snake_case via `@SerialName`): `MarketList`, `MarketApp`, `MarketDetail`, `BundleOption`, `CatalogPatch(name, description, defaultEnabled: Boolean?)`, `FeedStatus`, `AppObject`, `AppStatusDto`, `Download`, `Candidate`, `AppDetail` (+ `AppSettings`), `PatchesView` (today's patches shape), `Schedule`, `OtaSchedule`, `CustomSource`, `JobId`. Delete `Catalog`, `Group`, `Item` (replaced by `AppObject`).
  `api/RelumeApi.kt`: `marketplace(q, category, origin, sort, page)`, `marketApp(pkg)`, `apps()`, `app(id)`, `enable(pkg, bundleId, patches, download)`, `putApp(id, body)`, `disable(id)`, `patches(id)`, `putPatches(id, body)`, `check(id)`, `build(id)`, `locate(id)`, `artifacts(id)`, `schedule()`, `putSchedule(body)`, `runSchedule()`, `otaSchedules()`, `createOtaSchedule(body)`, `putOtaSchedule(id, body)`, `deleteOtaSchedule(id)`, `devices()`, `sources()`, `addSource(url, engine, assetRegex, packages)`, `deleteSource(id)`; kept: health, auth, `/api/me/*`, plan, receipts, jobs, job log SSE, downloads, projects, assignments, cleanup. Removed: `catalog`, `item`, `setSwitches`, `update`, `checkAll`.
- `AppModel` holds `apps: List<AppObject>`, the schedule, install state (existing `beginInstall` flow keyed by app id); `MarketModel` holds the marketplace list, filters and the selected app detail.

**Behavior:** spec §8.1 and §8.2 fully; spec §8.3 only as thin first versions: Marketplace = search + list with tiles and bundle count; MarketApp = bundle list (read-only); Apps = list with status pill, check and rebuild buttons, Install button wired to the existing `beginInstall` (after assigning the app to this device, spec §8.3); AppSettings = enable switch + version mode; Schedules = interval text + Run now. Typography per `android/tokens.md` §4; no `isSystemInDarkTheme`; no new `Build.VERSION.SDK_INT` check.

**Tests (commit):** `MarketParseTest.kt` with `marketplaceListParses`, `appObjectParses`, `scheduleParses`, each decoding a JSON sample copied from API part §2.1/§2.2/§2.3 (with spec §6 amendments) and asserting the typed fields, including `defaultEnabled == null` and `download.candidates`.

**Acceptance:**
- [ ] `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:testDebugUnitTest --tests 'io.github.jhangyu.relume.api.*' --console=plain --no-watch-fs > .claude/tmp/mp-1.5/g.log 2>&1; echo RC=$?` → `RC=0` (this compiles all main sources), and `MarketParseTest` appears in `android/app/build/test-results/testDebugUnitTest/*.xml`. The full Android leg runs in the round-1 gate.
- [ ] `grep -rnE "isSystemInDarkTheme|darkColorScheme|dynamicLightColorScheme|dynamicDarkColorScheme|DayNight|FluentTheme|object Icons|\bIc\(|setSwitches|checkAll|/api/items|/api/catalog\"|autoUpdate" android/app/src` → no hit.
- [ ] `ls android/app/src/main/res/drawable/ms_*.xml | wc -l` → ≥ 24; `ls android/app/src/main/kotlin/io/github/jhangyu/relume/ui/DetailScreen.kt` → "No such file".
- [ ] `grep -r SDK_INT android/app/src/main | wc -l` → equals `git grep SDK_INT 0d55fc3 -- android/app/src/main | wc -l`.

**Targeted command:** `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:testDebugUnitTest --tests 'io.github.jhangyu.relume.api.*' --console=plain --no-watch-fs` (warm daemon; if the first cold run exceeds the foreground limit, report BLOCKED with the elapsed time).

**Steps:**
- [ ] Write `MarketParseTest` with the three samples; run → fail (no DTOs).
- [ ] `Models.kt` + `RelumeApi.kt`; run → green.
- [ ] Convert the 24 official SVGs from `android/icons-material.js` to vector drawables (path data unchanged).
- [ ] `Theme.kt`, `themes.xml`, `Components.kt`; port Login, Settings, Install, Log.
- [ ] `Screen.kt`, `RelumeRoot.kt` (NavigationBar with per-tab indicator colours, avatar → Settings), thin Marketplace/MarketApp/Apps/AppSettings/Schedules, `MarketModel`, `AppModel` port; delete `DetailScreen.kt`, `BuildsScreen`.
- [ ] Targeted Android command (acceptance 1), greps; commit.

## 8. Round 2 tasks

### Shared scratch render check (web tasks 2.1–2.3 and the reviewer)

Not committed. Task 2.1 writes it in its first hour at `.claude/tmp/mp-web/render.py`; tasks 2.2/2.3 and the reviewer run it read-only. It serves a copy of the migrated live DB (plan §5 step 4 output `.claude/tmp/mp-migrate/data`) in-process, bypasses login for this scratch process only, and asks headless Chrome for each page. One foreground command, about 2 s per page.

```python
# usage: python3 .claude/tmp/mp-web/render.py <data_dir> <path>... ; exit 0 = every page passed
import os, pathlib, shutil, subprocess, sys, tempfile, threading
ROOT = pathlib.Path(__file__).resolve().parents[3]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
sys.path.insert(0, str(ROOT / "local"))
work = pathlib.Path(tempfile.mkdtemp(prefix="render-", dir=ROOT / ".claude/tmp/mp-web"))
shutil.copytree(sys.argv[1], work / "data")
os.environ["RELUME_DATA"], os.environ["RELUME_KEY"] = str(work / "data"), str(work / "key")
from webui import __main__ as main, auth, server
auth.session_token = lambda cookie: "scratch"     # scratch only: every request counts as signed in (verify server.py uses it for pages too)
srv = server.make_server(main.boot(), "127.0.0.1", 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
bad = 0
for path in sys.argv[2:]:
    p = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--enable-logging=stderr", "--v=0",
                        f"--user-data-dir={work / 'chrome'}", "--virtual-time-budget=3000", "--dump-dom",
                        f"http://127.0.0.1:{srv.server_port}{path}"], capture_output=True, text=True, timeout=30)
    errors = [l for l in p.stderr.splitlines() if "Uncaught" in l or "Failed to load resource" in l]
    ok = p.returncode == 0 and "<main" in p.stdout and not errors
    print(("ok " if ok else "FAIL ") + path, *errors[:5], sep="\n  ")
    bad += not ok
srv.shutdown(); shutil.rmtree(work)
sys.exit(1 if bad else 0)
```

Run: `python3 .claude/tmp/mp-web/render.py .claude/tmp/mp-migrate/data / /apps.html > .claude/tmp/mp-2.x/render.log 2>&1; echo RC=$?`. If `.claude/tmp/mp-migrate/data` is missing, ask the lead (it is produced by the round-1 gate). The Marketplace needs catalog rows: run `POST /api/catalog/refresh` once on your copy (the script may call `catalog.refresh` before serving; network access to the official feed is allowed in scratch).

### Task 2.1: Web foundation and Marketplace page

**Goal:** All web pages share one Fluent 2 light stylesheet, an inline SVG icon sprite and the new navigation; the Marketplace page (`/`) lists the catalog and enables apps (spec §7.1, §7.3).

**Read:** spec §2.5–§2.7, §6.1, §6.2, §6.5, §7.1–§7.3, §12 items 7, 11, 22, 23; mockups `web/marketplace.html`, `web/fluent.css`, root `icons.js`, root `tokens.md` §Web (all at `0d55fc3`); code: `static/app.js`, `static/app.css`, `static/index.html`, `static/index.js`, `tests/test_ui_static.py`; `docs/logs/2026-10-26-explore-frontend.md` §1, §5, §6.

**Files:**
- Rewrite: `local/webui/static/app.css`, `local/webui/static/app.js`, `local/webui/static/index.html`, `local/webui/static/index.js`
- Modify: `local/webui/tests/test_ui_static.py`, `local/webui/static/manifest.webmanifest` (only if a colour changes)
- Scratch: `.claude/tmp/mp-web/render.py` (above)

**Interfaces:**
- Consumes: round-1 routes (spec §6) on main.
- Produces (**first commit**, send hash to the lead): `app.css` with the `web/fluent.css` token block and component class names unchanged (`.btn`, card, pill, badge, drawer, dialog, table classes exactly as in the mockup) plus utilities still used by kept pages; `app.js` exports kept (`esc`, `html`, `$`, `$$`, `api`, `act`, `toast`, `logStream`, `fmt`, `every`, `ask`, `tabs`, `shell`, `iconTile`, `JOB_BADGE`, `fillDownloads`) plus `icon(name)` and `STATUS` for the spec §6.2 states; `catalogItems` and the theme toggle removed; `shell()` renders the spec §7.1 nav (Marketplace `/`, Apps `/apps.html` with a count badge from `GET /api/apps`, Schedules, group "Manage": Builds, Devices, Followed projects, Settings); `test_ui_static.py` updated (NAV list, deleted tests of plan §3, `item.*` checks removed, `apps` and `index` dropped from the old-mockup href regex of `test_no_network_refs_or_mockup_names` at line 39). Every page except login keeps `<aside class="side" aria-label="Primary">` and `#toast`.

**Behavior:** spec §7.1 and §7.3 exactly; Enable is enabled when a selectable bundle is chosen; URL fields optional; featured banners computed client-side (spec §2.7); icon-only buttons have `aria-label` + `title`; the dark-mode removal list of spec §7.1 is done in `app.css`, `app.js`, `index.html` (each other page's head bootstrap script is removed by its owner task 2.2/2.3).

**Acceptance:**
- [ ] `cd local && python3 -m unittest webui.tests.test_ui_static -v > ../.claude/tmp/mp-2.1/t.log 2>&1; echo RC=$?` → `RC=0` (on the first commit and at the end).
- [ ] `node --check local/webui/static/app.js && node --check local/webui/static/index.js; echo RC=$?` → `RC=0`.
- [ ] `grep -nE "data-theme|prefers-color-scheme|relume-theme|catalogItems|/api/catalog\"|/api/catalog'" local/webui/static/app.js local/webui/static/app.css local/webui/static/index.html local/webui/static/index.js` → no hit.
- [ ] `grep -c "<symbol id=\"i-" local/webui/static/app.js` → ≥ 37 (root `icons.js` set).
- [ ] Render check: `python3 .claude/tmp/mp-web/render.py .claude/tmp/mp-migrate/data / > .claude/tmp/mp-2.1/render.log 2>&1; echo RC=$?` → `RC=0`.

**Targeted command:** the unittest command (< 5 s), `node --check`, the render check (< 30 s).

**Steps:**
- [ ] Port `web/fluent.css` into `app.css`; sprite + `icon()` + nav into `app.js`; update `test_ui_static.py`; run; **first commit**.
- [ ] Write `render.py`; Marketplace markup from `web/marketplace.html`; `index.js` data wiring (`GET /api/marketplace` with filters, `GET /api/marketplace/{pkg}` on expand, `POST /api/apps`, custom sources, catalog refresh); run checks; commit.

### Task 2.2: Web Apps page

**Goal:** `apps.html` lists enabled apps with status and actions and edits one app in a settings drawer (spec §7.4); `item.html`/`item.js` are gone.

**Read:** spec §6.2, §6.3, §7.4, §5.4; mockup `web/apps.html`; code at `0d55fc3`: `static/item.js` (patch list, `fieldFor`, `overridesFrom`, version tab, variants, module tab), `static/index.js:23-43` (row, status, download menu), `static/app.js` (after task 2.1's first commit).

**Files:**
- Create: `local/webui/static/apps.html`, `local/webui/static/apps.js`
- Delete: `local/webui/static/item.html`, `local/webui/static/item.js`

**Interfaces:**
- Consumes: task 2.1 first commit (`app.css` classes from `web/fluent.css`, `icon()`, `STATUS`, `fillDownloads`, `every`, `ask`); routes `GET /api/apps`, `GET|PUT|DELETE /api/apps/{id}`, `POST /api/apps` (re-enable), `GET|PUT /api/apps/{id}/patches`, `POST /api/apps/{id}/check|build|locate`, `GET /api/apps/{id}/artifacts`, `GET /api/marketplace/{pkg}` (bundle choice), `GET /api/projects` (dependencies), `GET /api/schedule`, `POST /api/schedule/run`, `POST /api/apps/{pkg}/icon/refresh`.
- Produces: page `/apps.html`; `?app=<id>` opens the drawer of that app (used by Marketplace "Enabled" links and Builds).

**Behavior:** spec §7.4 exactly. Save sends one `PUT /api/apps/{id}` (settings + download) and, when patches changed, one `PUT /api/apps/{id}/patches`; "Save and rebuild" then calls `POST /api/apps/{id}/build`. Stale overrides from `settings.stale_overrides` are listed, not hidden. The page has no theme bootstrap script.

**Acceptance:**
- [ ] `cd local && python3 -m unittest webui.tests.test_ui_static -v > ../.claude/tmp/mp-2.2/t.log 2>&1; echo RC=$?` → `RC=0` (every `$('#id')` of `apps.js` exists in `apps.html`).
- [ ] `node --check local/webui/static/apps.js; echo RC=$?` → `RC=0`.
- [ ] `ls local/webui/static/item.html local/webui/static/item.js` → 2 × "No such file"; `grep -rn "item.html" local/webui/static` → no hit.
- [ ] `grep -nE "data-theme|prefers-color-scheme|/api/items" local/webui/static/apps.html local/webui/static/apps.js` → no hit.
- [ ] Render check `python3 .claude/tmp/mp-web/render.py .claude/tmp/mp-migrate/data /apps.html "/apps.html?app=1"` → `RC=0`.

**Targeted command:** as above (< 30 s).

**Steps:**
- [ ] Markup from `web/apps.html` with `app.css` classes; port patch list/options/version/variants/module editors from `item.js@0d55fc3`; wire routes; run checks; delete `item.*`; commit.

### Task 2.3: Web Schedules, kept pages and docs

**Goal:** Schedules shows the global interval and OTA schedules (spec §7.5); Builds, Devices, Followed projects, Settings and Login are restyled and call the new routes (spec §7.6); docs describe Marketplace instead of the seed.

**Read:** spec §6.3, §6.6, §7.1, §7.5, §7.6, §9; mockups `web/schedules.html`, `web/restyle-sample.html`; code: `static/{schedules,builds,devices,projects,settings,login}.{html,js}`, `README.md`, `docs/Frequently Asked Questions.md`; `docs/logs/2026-10-26-explore-frontend.md` §4, §5.

**Files:**
- Modify: `local/webui/static/schedules.html`, `schedules.js`, `builds.html`, `builds.js`, `devices.html`, `devices.js`, `projects.html`, `projects.js`, `settings.html`, `settings.js`, `login.html`, `login.js`, `README.md`, `docs/Frequently Asked Questions.md`

**Interfaces:**
- Consumes: task 2.1 first commit; routes `GET|PUT /api/schedule`, `POST /api/schedule/run`, `/api/schedules*` (OTA), `POST /api/schedules/preview`, `GET /api/apps`, `POST /api/apps/{id}/build|check`, `POST /api/projects/{id}/pull`, `GET /api/jobs*`, `GET /api/devices`, settings/notify/cleanup routes.
- Produces: nothing other tasks use.

**Behavior:** spec §7.5 (presets, Custom = day/week/hours forms, no cron, pipeline, OTA list + dialog) and §7.6 (retry rules by job target, assign dialog from `GET /api/apps`, project pull route, Settings without Appearance and "CLI update", cleanup text); every page loses its theme bootstrap script and dark `theme-color` meta. Docs: README catalog paragraph rewritten (Marketplace, built-in + community catalog + custom sources, global schedule), `export-seed` and seed sentences removed; FAQ lines 23–25 removed.

**Acceptance:**
- [ ] `cd local && python3 -m unittest webui.tests.test_ui_static -v > ../.claude/tmp/mp-2.3/t.log 2>&1; echo RC=$?` → `RC=0`.
- [ ] `for f in schedules builds devices projects settings login; do node --check local/webui/static/$f.js || exit 1; done; echo RC=$?` → `RC=0`.
- [ ] `grep -nE "data-theme|prefers-color-scheme|relume-theme|catalogItems|/api/items|/api/upstreams|/api/catalog\"|kind: *'app'|o-upd|o-bld|o-force|gh-cli" local/webui/static/{schedules,builds,devices,projects,settings,login}.{html,js}` → no hit.
- [ ] `grep -nE "export-seed|catalog-seed|seed" README.md "docs/Frequently Asked Questions.md"` → no hit.
- [ ] Render check `python3 .claude/tmp/mp-web/render.py .claude/tmp/mp-migrate/data /schedules.html /builds.html /devices.html /projects.html /settings.html` → `RC=0`.

**Targeted command:** as above (< 30 s).

**Steps:**
- [ ] Schedules page from `web/schedules.html`; run checks; commit.
- [ ] Builds, Devices, Projects, Settings, Login restyle + route changes; run checks; commit.
- [ ] README + FAQ; grep; commit.

### Task 2.4: Android Marketplace

**Goal:** The Marketplace tab, the app detail with bundle and patch choice, the download-source step and the add-custom-source dialog behave as on web (spec §8.3 first bullet), styled per `android/tokens.md`.

**Read:** spec §2.5, §2.7, §6.1, §6.2, §6.5, §8.1–§8.3, §12 items 7, 19; `android/marketplace.html`, `android/tokens.md`, `android/m3.css`; code (round-1 output): `ui/MarketModel.kt`, `ui/MarketplaceScreen.kt`, `ui/MarketAppScreen.kt`, `ui/Components.kt`, `ui/Theme.kt`, `api/RelumeApi.kt`, `api/Models.kt`. Confirm Compose APIs with context7 `query-docs` for the BOM's material3 version before use (`SearchBar`, `FilterChip`, `HorizontalMultiBrowseCarousel`, `RadioButton`, `Switch`, `OutlinedTextField`, `AlertDialog`).

**Files (`K` = `android/app/src/main/kotlin/io/github/jhangyu/relume/`; expand `K` in every command below):**
- Modify: `K/ui/MarketModel.kt`, `K/ui/MarketplaceScreen.kt`, `K/ui/MarketAppScreen.kt`
- Create: `K/ui/SourceDialog.kt`; `android/app/src/test/kotlin/io/github/jhangyu/relume/ui/MarketFilterTest.kt` only if `MarketModel` holds client-side filter/featured logic (the featured selection of spec §2.7 is core logic)

**Interfaces:**
- Consumes: task 1.5 frozen signatures (`MarketplaceScreen`, `MarketAppScreen`, `MarketModel`, components, theme, `RelumeApi.marketplace/marketApp/enable/putApp/sources/addSource/deleteSource`).
- Produces: nothing new for other tasks; signatures unchanged. `RelumeRoot.kt` is not edited (owned by round 1; a needed change goes to the lead).

**Behavior:** search bar, category chips (pastel per category), featured carousel (spec §2.7: top 3 by bundles, built-in, 3 most recently updated), two-line list with tile, category, bundle count, "On" when enabled; app detail: description, versions, bundle radio list (origin label, stars, patch count; disabled with `notSelectableReason`), patch switches (default from `defaultEnabled`, `null` → "default unknown", "Changed by you" pill); "Next: download source" → state message (`download.state`), optional APKMirror and APKPure fields, "Enable and build" enabled when a selectable bundle is chosen → `enable(...)`, then `onEnabled(appId)`; add-custom-source dialog from the top bar (`ms_add_link`): URL, engine (Morphe / LSPatch / NPatch), asset regex + target packages for Xposed engines, server error text shown as is.

**Acceptance:**
- [ ] Targeted Android command (below) → `RC=0` (compiles all sources; `MarketFilterTest` passes if created).
- [ ] `grep -nE "dynamicLightColorScheme|isSystemInDarkTheme|Color\(0x" <the four task files>` → no hit (colours come from `MaterialTheme.colorScheme` / `LocalRelumeColors`).
- [ ] `grep -n "enable(" K/ui/MarketAppScreen.kt K/ui/MarketModel.kt` → at least 1 hit; the reviewer reads that the "Enable and build" button's `enabled` expression depends only on the bundle choice.

**Targeted command:** `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:testDebugUnitTest --tests 'io.github.jhangyu.relume.ui.*' --console=plain --no-watch-fs > .claude/tmp/mp-2.4/g.log 2>&1; echo RC=$?` (warm daemon; a cold run over the foreground limit → BLOCKED with the elapsed time).

**Steps:**
- [ ] If featured/filter logic lives in `MarketModel`, write `MarketFilterTest` first; run → fail; implement → green.
- [ ] Marketplace list and carousel; app detail; download-source step; source dialog; run the targeted command; commit.

### Task 2.5: Android Apps, Install, App settings, Schedules, Settings

**Goal:** The Apps tab shows build status and this phone's install state with Install/Update (no Download); App settings edits the same fields as web; Schedules edits the global interval and OTA schedules; Settings opens from the avatar (spec §8.2, §8.3 bullets 2–4).

**Read:** spec §6.2, §6.3, §7.4 (field list), §8.1–§8.3, §12 items 18, 25; `android/apps.html`, `android/schedules.html`, `android/tokens.md`; code (round-1 output): `ui/AppModel.kt` (existing `beginInstall`, prompts, plan evaluation), `ui/AppsScreen.kt`, `ui/AppSettingsScreen.kt`, `ui/SchedulesScreen.kt`, `ui/SettingsScreen.kt`, `ui/InstallScreen.kt`, `ui/AppStatus.kt`, `install/*`, `inventory/Inventory.kt`, `api/*`. Confirm Compose APIs with context7 before use (`SegmentedButton`, `ModalBottomSheet`, `ExtendedFloatingActionButton`, `TooltipBox`, `LinearProgressIndicator`).

**Files (`K` as above):**
- Modify: `K/ui/AppModel.kt`, `K/ui/AppsScreen.kt`, `K/ui/AppSettingsScreen.kt`, `K/ui/SchedulesScreen.kt`, `K/ui/SettingsScreen.kt`, `K/ui/InstallScreen.kt`, `K/ui/AppStatus.kt`
- Create only if the logic is non-trivial: `android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt` (install-state rule: not installed / update / installing / installed + version) and `ScheduleSpecTest.kt` (preset ↔ spec mapping)

**Interfaces:**
- Consumes: task 1.5 frozen signatures and API client; existing `InstallRunner`, `AppModel.beginInstall` flow, `RelumeApi.putAssignments`, `plan`.
- Produces: nothing new for other tasks; signatures unchanged; `RelumeRoot.kt` not edited.

**Behavior:** spec §8.3 bullets 2–4. Install/Update: if the app id is not in this device's assignments, `putAssignments(deviceId, current + appId, currentProjects)` first, then the existing plan/prompt/run flow (signer and dependency prompts kept). Install state per card: from the plan step / inventory as today (`AppFacts`). Status pill colours per `android/tokens.md` §3 "Build status pill". App settings: enable switch (off = `disable`), bundle radio list (`marketApp(pkg)`), patches + options (`patches`/`putPatches`, editors for the existing option types), version mode `SegmentedButton` Auto/Pinned/Latest + pin field + allow nearest, Follow-project dependencies, download URLs with state, candidate pick, paste; bottom bar "Save" and "Save and rebuild". Schedules: interval chips 1 h/3 h/6 h/12 h/24 h → `putSchedule({spec: {every: hours, hours: n}})`, Custom sheet (day/week/hours), next run, Run now, last-run pipeline; OTA list with switches, FAB → bottom sheet (name, repeat, at, retries, phones from `devices()`). Settings: existing sections reached from the avatar, with a back arrow; cleanup text "apps that are not enabled".

**Acceptance:**
- [ ] Targeted Android command (below) → `RC=0`.
- [ ] `grep -nE "Download\b|openDownload" K/ui/AppsScreen.kt` → no hit (no Download button; downloads happen inside the install run).
- [ ] `grep -n "putAssignments" K/ui/AppModel.kt` → at least 1 hit.
- [ ] `grep -nE "dynamicLightColorScheme|isSystemInDarkTheme|Color\(0x" K/ui/AppsScreen.kt K/ui/AppSettingsScreen.kt K/ui/SchedulesScreen.kt K/ui/SettingsScreen.kt K/ui/InstallScreen.kt` → no hit.

**Targeted command:** `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:testDebugUnitTest --tests 'io.github.jhangyu.relume.ui.*' --tests 'io.github.jhangyu.relume.install.*' --console=plain --no-watch-fs > .claude/tmp/mp-2.5/g.log 2>&1; echo RC=$?`.

**Steps:**
- [ ] Install-state and schedule-preset rules: tests first if created; implement in `AppModel`/`AppStatus` → green.
- [ ] Apps card + Install row; App settings; Schedules + sheets; Settings via avatar; run the targeted command; commit.

## 9. Self-review (plan author)

- Spec coverage: spec §2 → 1.3; §3 → 1.2; §4 → 1.1; §5, §6 → 1.4 (§6.6 → 1.3); §7.1–§7.3 → 2.1; §7.4 → 2.2; §7.5–§7.6 and §9 docs → 2.3; §8.1–§8.2 → 1.5; §8.3 → 1.5 (thin) + 2.4 + 2.5; §10 → plan §3; §11 → plan §5 and contract out-of-scope; §12 → reviewer dimension 1.
- File ownership: no file is in two tasks of one round (round 1: `server.py` only in 1.4; `upstream.py`, `projects.py`, `settings.py` only in 1.3; `catalog_rows.py` only in 1.1; `__main__.py` only in 1.2. Round 2: `test_ui_static.py` only in 2.1; `RelumeRoot.kt`, `Screen.kt` in no round-2 task; `AppModel.kt` only in 2.5; `MarketModel.kt` only in 2.4).
- Interface names checked across tasks: `market_apps`, `app_bundles`, `feed_status`, `default_enabled`, `not_selectable_reason`, `list_enabled`, `list_artifacts`, `ensure_update`, `put_download`, `download_state`, `reset_lookup`, `HttpError(status, message, code=None, **extra)`, `http_get(...) -> (status, headers, body, final_url)`.
- Deviation from the writing-plans template: steps name the code to write and the tests by exact method name, but do not inline full source for every step; the spec and the three part files carry the exact behaviour, SQL shapes and signatures, and inlining several thousand lines here would duplicate them.
