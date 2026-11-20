# App Manage preflight (read-only), 2026-11-20

Spec: `docs/superpowers/specs/2026-11-20-app-manage-design.md`. Base: main `ad0c1a4`. All claims below were read in code.

## 0. Spec contradictions / corrections
1. **Sync cadence.** Spec says "periodic sync (~15 min)". Code: `CheckInterval { OFF, H1, H6, H24, DAILY }` (`android/.../api/Prefs.kt:5`), WorkManager periods 1/6/24 h (`sync/CheckScheduler.kt:30-31,52,60`). No 15-minute path exists. Cells will lag up to the user's chosen interval (default not checked). Needs a user decision (shorter interval for action pickup, or a push channel) or the "waiting for phone sync" copy stays but the latency claim is wrong.
2. **`QUERY_ALL_PACKAGES` already declared** (`AndroidManifest.xml:8-11`, lint-suppressed). Spec says "Declares" as new work. Only `PACKAGE_USAGE_STATS` (usage access) is missing; `REQUEST_DELETE_PACKAGES` and `POST_NOTIFICATIONS` already exist (manifest :6, :12).
3. **Companion report today is tracked-only.** `GET /api/me` returns `tracked_packages`; the app reads only those (`inventory/Inventory.kt:36-39,77-80`); the server drops everything else (`ota.py:97-108 store_reported` keeps `tracked_packages()`). Spec's "only changed entries, user apps only" is a new, additive channel; `_parse_sync` (`ota.py:527-559`) ignores unknown body keys, so an `inventory` key can ride the same POST without breaking old apps.
4. **Companion install pipeline is plan-step based, single `base.apk`** (`install/Installer.kt:18-52`: `create(context, step: PlanStep, size)`, `write` opens one `base.apk`). `play_direct` (base + splits in one session) and `remove` outside a plan need new code; the existing uninstall receipt path is bound to run `revision` + step index (`Installer.kt:68-80`, `InstallResultReceiver`), so a new receiver action is needed, not a reuse.
5. **Jobs: one worker thread per kind, kinds fixed in `jobs.OPTION_DEFAULTS`** (`jobs.py:17-21`, started `jobs.py:392-400`). An "ADB action" job needs a new kind registered there plus `jobs.register(...)`; otherwise runs would serialize behind `ota`.
6. `icons` data subdir already exists (`db.py:9`) and is used by catalog icons (`catalog.py:920-1000`); app-icon store should use a distinct subfolder/name scheme.

## 1. Server map
- **Migrations**: `local/webui/schema/NNN_name.sql`, latest `023_ota_installs_patches_version.sql` → next is **024**. `db.migrate` (`db.py:70-84`) runs files with number > `PRAGMA user_version` via `executescript`, sorted by 3-digit prefix. Test template DB is migrated once per process (`tests/helpers.py:_template`).
- **Settings store**: `settings.get(key, default)` / `settings.put(key, value, c=None)` JSON-valued (`settings.py:25-39`). Primary device id fits here; validate against `devices` on write. Google Play creds: `settings.google_play()` → `{email, aas_token}` or None (`settings.py:98-110`).
- **Routes**: each module exposes `ROUTES = [(method, pattern, fn)]`; `server.MODULES` tuple (`server.py:17`) lists modules, `CHANGES[module]` (`server.py:21-23`) lists SSE topics published after an authenticated mutating request to that module's routes; make_server fails without an entry (`server.py:383`). Handlers return `(status, obj)`, `FileResponse`, `StreamResponse`, `SSEResponse`. `req.device_id` set for companion tokens, `req.session` for web.
- **SSE**: `events.TOPICS = ('jobs','apps','projects','devices','schedules')` (`events.py:8`); `events.changed(*topics)` raises on unknown topic (`:62-65`) → add `'appmanage'` to TOPICS. `/api/me/*` mutating routes publish the **owning module's** CHANGES topics only; companion POSTs that must refresh the page need an explicit `events.changed('appmanage')` in the handler (or their routes in the appmanage module with `CHANGES['appmanage']=('appmanage',)`).
- **Auth**: companion bearer = `auth.authenticate(headers)` → `(session, device_id)` (`auth.py:124`). Paths `/api/me` and `/api/me/*` demand a device token (`server.py:~228-231`); other paths accept either. `auth.SESSION_ONLY` (`auth.py:19`) forbids device tokens on listed web-only routes (set-primary, create/cancel actions should be added so a companion token cannot reach them). `auth.PUBLIC` (`:16`). Legacy tokens (no `device_uid`) are 403 except `LEGACY_ALLOWED` (`auth.py:24-30`): new `/api/me/*` routes are auto-denied to legacy, which is the right default. Existing `_me(req)` = `devices.get(req.device_id)` (`ota.py:446`).
- **Sync handler**: `ota.sync` (`ota.py:580-608`) → `_parse_sync` (`:527`) → `store_reported` (`:97`) → response `_sync_json` (`:467`: `{device, tracked_packages, plan}`). Companion call order is `GET /api/me` then `POST /api/me/sync` (`Inventory.kt:77-83`). Other companion routes: `/api/me/plan`, `/api/me/installs` (`post_install`, `:665`, accepts only kind item/project, results installed/failed), `/api/me/adopt` (`:690`), listed `ota.py:716-723`.
- **Hazards for new tables**: `ota.adopt` (`ota.py:690-713`) moves `ota_installs`/`device_packages` from the phone's new row onto the adopted ADB row then deletes the new row — new inventory/action tables need the same move (or FK `ON DELETE CASCADE` + explicit move). `devices.delete_device` (`devices.py:469-479`) deletes `ota_installs` by hand; FK tables cascade (`PRAGMA foreign_keys=ON`, `db.py:conn`). `_leave_legacy` (`ota.py:562-577`) deletes a legacy row (cascade would drop its rows).
- **ADB work**: `devices.adb(args, serial=, timeout=, cancel_event=, log=)` is the only adb entry, one lock per serial (`devices.py:116-127`); raises `AdbTimeout`. `devices.address(dev)`, `devices.reconnect`, `devices.online_states()` (`:316`), `devices.is_app(dev)`. Package read: `ota._present(dev)` (`ota.py:326`) runs `pm list packages -f --show-versioncode`, parsed by `devices.parse_packages` (`devices.py:43`) — tracked packages only get sha256. `ota.handle(job, log)` (`:877`) is the job-runner pattern (reconnect → plan → steps → `_present`). Install over adb: `ota._install` (`:756`). There is no `dumpsys`/size/label reader yet.
- **Page serving**: `PAGE = ^/([a-z]+\.html)$` (`server.py:31`) serves any existing `static/<name>.html` behind the session cookie (login.html public); assets `/static/<[A-Za-z0-9_-]+>.(css|js|svg|png|webmanifest)` (`:32`). So `appmanage.html` (letters only, no hyphen) needs no allowlist edit. Sidebar is hard-coded in `static/app.js` `shell()` (`app.js:300-310`, `<a href="/devices.html">` at :308), current page via `aria-current`; icons are an inline sprite `<symbol id="i-…">` in `app.js:216+` (add a Fluent symbol via `python3 ~/ui-kits/find-icon.py`). SSE subscription helper `live(topics, fn)` (`app.js:142`).
- **Static safety test**: `tests/test_ui_static.py` forbids raw `innerHTML=` without `html\`` (`:5`) — page JS must use the `html` tag.
- Model for a new page: `devices.html`/`devices.js` (49/237 lines; head, `.shell`/`aside.side`/`.main/.header`, `<script type=module>`).

## 2. Android map (`android/app/src/main/kotlin/io/github/jhangyu/relume/`)
- **Sync**: `inventory/Inventory.kt` `sync()` (`:77`) used by three callers: `sync/CheckWorker.kt:25` (periodic), `install/InstallWorker.kt:174`, `ui/AppModel.kt:201` (on open → "opening the companion syncs at once" is true). `SyncResult` holds the plan; `Inventory.read` hashes tracked packages only (`:36-39`).
- **API client**: `api/RelumeApi.kt` (345 lines) `sync()` `:51`, `me()` `:49`, `receipt()` `:63`, `openDownload(path, from, ifRange)` `:195`; DTOs in `api/Models.kt` (682 lines; `InstalledPackage` `:94`, `SyncReport` `:115`, `SyncResponse` `:244`). kotlinx-serialization with `@SerialName`.
- **Install**: `install/Installer.kt` (79 lines: session create/write/commit/abandon/uninstall), `InstallRunner.kt` (entry, state flow, run file), `InstallRun.kt` (pure state machine, 387 lines), `InstallWorker.kt` (234), `Downloader.kt` (resume/range download of a plan `Download`), `InstallResultReceiver.kt` (actions install/uninstall), `RemovedApps.kt` (seen-installed bookkeeping).
- **Notifications**: `sync/Notifications.kt` — single channel `install` (`:20,30-36`), `post()` helper (`:62-72`), `openApp()`; `ensureChannels` called from `RelumeApp.kt`. `play_page` notification can reuse `post` with a Play-Store intent (new method).
- **Manifest** (`AndroidManifest.xml`): INTERNET, REQUEST_INSTALL_PACKAGES, REQUEST_DELETE_PACKAGES, QUERY_ALL_PACKAGES, POST_NOTIFICATIONS, UPDATE_PACKAGES_WITHOUT_USER_ACTION. **Missing**: `PACKAGE_USAGE_STATS` (tools:ignore ProtectedPermissions) for `StorageStatsManager`.
- **UI for "grant usage access" button**: `ui/SettingsScreen.kt` (239 lines) already has permission-style rows, e.g. notifications `ActionButton("Allow", askNotifications, kind=Outlined)` (`:114`), driven from `ui/RelumeRoot.kt:78,132`. Navigation is a sealed `Screen` (`ui/Screen.kt:7-14`) + `TABS` (`RelumeRoot.kt:55-60`). Usage-access state must be re-read on resume (`AppOpsManager`).
- **Android tests**: `scripts/test.py` android leg = `android/gradlew -p android testDebugUnitTest` with `ANDROID_HOME` required (`scripts/test.py`, android_leg); JUnit 4 only (`app/build.gradle.kts:100`, no Robolectric, no mockito) → anything testable must be a pure Kotlin function (pattern already used: `InstallRun`, `DownloadDecision`, `BackgroundStart`). minSdk 29, targetSdk 37, compileSdk 37 (`build.gradle.kts:48-53`).

## 3. Tests
- `python3 scripts/test.py` legs: `python` (each `local/webui/tests/test_*.py` module runs in its own child process, `PYTHON_WORKERS_MAX=4`), `shell` (`src/build/test-build-item.sh`), `android`. Budget 90 s total, 1 s/test, `sleep|delay(` grep in tests is a hard fail (`FORBIDDEN`). Mac/Linux only.
- Layout: `local/webui/tests/` (36 `test_*.py` + `helpers.py`, `device_helpers.py`, `catalog_rows.py`, `fixtures/`). `helpers.DataTestCase` copies a once-migrated template DB per test and resets auth/events/jobs. `device_helpers`: `add_device`, `add_app_device(token_hash=…)`, `report`, `assign`, `add_catalog_item`, `add_job`, `FakeAdb` (rule-based adb stub), `FakeLog`, `req()` (fake request namespace).
- Existing tests touching ota/devices: `test_device_ota.py`, `test_device_otajob.py`, `test_device_app.py` (companion endpoints), `test_device_routes.py`, `test_device_adb.py`, `test_device_notify.py`, `test_legacy_client.py`, `test_core_auth.py`, `test_core_sse.py` (TOPICS), `test_core_db.py` (migrations), `test_core_router.py`, `test_core_jobs.py`, `test_core_scheduler.py`, `test_assign_disabled.py`, `test_reenable_clash.py`, `test_serverremove.py`, `test_projects.py`, `test_market_api.py`. **Check before adding a topic/kind/migration**: `test_core_sse.py`, `test_core_db.py`, `test_core_jobs.py`, `test_core_router.py` (may enumerate topics/modules/schema count) — not read in detail.

## 4. Hotspots and suggested split
Hotspot files (single owner each in any round):
| File | Touched for |
|---|---|
| `local/webui/server.py` (MODULES, CHANGES) | register module |
| `local/webui/events.py` (TOPICS) | `appmanage` topic |
| `local/webui/jobs.py` (OPTION_DEFAULTS) | ADB action job kind |
| `local/webui/ota.py` (`_parse_sync`, `sync`, `_sync_json`, `adopt`) | inventory ingest, action pickup in sync response, adopt move |
| `local/webui/devices.py` (`delete_device`, parsers) | cascade, `pm`/`dumpsys` parsers |
| `local/webui/auth.py` (`SESSION_ONLY`) | web-only routes |
| `local/webui/static/app.js` (`shell()` nav, sprite) | sidebar entry + icon |
| `android/.../api/Models.kt`, `RelumeApi.kt` | DTOs + calls |
| `android/.../inventory/Inventory.kt`, `sync/CheckWorker.kt`, `install/InstallWorker.kt`, `ui/AppModel.kt` | report + action pickup hooks |
| `android/.../AndroidManifest.xml`, `sync/Notifications.kt`, `ui/SettingsScreen.kt`, `ui/RelumeRoot.kt` | permission, channel, button |
| `local/webui/schema/024_*.sql` | one new migration (single owner; freeze before parallel work) |

Suggested round 1 (after architect freezes: schema 024 DDL, matrix JSON, companion inventory/action/result JSON, function signatures):
1. **WP-A server store**: `schema/024_appmanage.sql` + new `appmanage_store.py` (inventory diff ingest, icon files, action table state machine: replace-unfinished, cancel, pickup transitions, result) + `tests/test_appmanage_store.py`. Owns 024.
2. **WP-B matrix logic** (pure, input = plain dicts): row ordering, highest-version/outdated incl. latest Relume build, cell state derivation + `tests/test_appmanage_matrix.py`. No DB access → fully parallel to A.
3. **WP-C ADB parsers + inventory reader**: pure parsers for `pm list packages -3`, `dumpsys package` labels/versions/sizes in `devices.py` (owner of devices.py) + fixtures + test. Sizes source for adb must be decided (see uncertainties).
4. **WP-D web page**: `static/appmanage.html/.js`, matrix CSS, `app.js` nav+icon, `docs/mockups/app-manage.html` copy; built against a fixture of the frozen matrix JSON. No server dependency.
5. **WP-E Android report side**: `Models.kt`/`RelumeApi.kt` DTOs+calls, `Inventory.kt` (user-app list, `StorageStatsManager`, icon hash/PNG), manifest permission, SettingsScreen button, pure-function tests. Owns all hotspot Android files in round 1.
6. **WP-F Play URL probe** (scratch only, spec preflight): decides `play_direct` vs in-app client; gate for round 2 Play work.

Round 2 (needs round 1 artifacts):
- **Server routes + wiring**: `appmanage.py` routes (matrix read, set primary, create/batch/cancel), `/api/me/*` inventory+icon+pickup+result, `server.py`/`events.py`/`jobs.py`/`ota.py`/`auth.py` edits, adopt/delete cascade, authz test (needs A, B contract).
- **ADB action job** (`adb install`/`uninstall`/`am start`, streaming Play download) (needs A, C, F).
- **Android action executor**: `play_direct` multi-APK session, `remove`, `play_page` notification, new receiver action, result report (needs E, server pickup shape; Play branch needs F).
- Integration gate + live proof (activation of real device sync).

## 5. In-flight work
- `git worktree list`: only `/Users/jhangyu/project/relume [main]`.
- `git log origin/main..main --oneline`: empty (no unpushed commits).
- `gh pr list --state open`: empty. Branches: only `main`, `origin/main`.
- Working tree: `M .gitignore`, many untracked `docs/logs/*`, untracked `--boot/` and others (truncated status; `scratch/` is gitignored `.gitignore:16`). Nothing unmerged touches the hotspot files; `git log` last touches: `ota.py` `93ca2a2`.

## Uncertainties
- adb app/data/cache sizes: no existing reader; `dumpsys diskstats` vs `du` needs a device probe (not run; no device access in this task).
- Whether `test_core_sse/db/jobs/router` assert exact TOPICS/modules/migration count — not read.
- `gh pr list` output was empty; not distinguished from auth failure (no error text shown).
- Default check interval value and whether any code path syncs more often than the 1 h minimum after a user action: only `AppModel.loadDeviceState` on open confirmed.
