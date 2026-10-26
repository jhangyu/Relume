# Polish preflight: backend (requirements 1 and 3), read-only
Paths relative to `local/webui/`. Code state: main HEAD 3abe409 plus untracked docs only; no code changed.

## Part A: Followed apps in the Apps list

### A1 Apps model and API
- App row = `item` row joined to `bundle`, `app`, `upstream`, `store_app` (`apps.py:21-25` ROW_SQL). Only `item.enabled = 1` rows are listed (`apps.py:83-86` list_enabled).
- Route `GET /api/apps` = `market.list_apps` (`market.py:109-110, 236`), which adds `download` = `apklocate.download_state(package)` to every app (`market.py:46-47`).
- Object shape (`apps.py:74-80`): id (item id), package, name, enabled, icon_url (`/api/apps/{package}/icon`), category, version_mode, bundle{bundle_id,name,origin,engine,tag}, app_version, status{state,job_id,progress,fail_code,fail_detail}; plus `download`. Detail `GET /api/apps/{id}` adds `settings`.
- Status states: queued, building, needs_download_url, failed, not_built, update_available, up_to_date (`apps.py:52-71`).
- Other per-app routes, all keyed by item id (`market.py:236-249`): PUT/DELETE `/api/apps/{id}`, patches, check, build, locate, artifacts. Icon route lives elsewhere (grep `icon` in catalog.py).
- Download of a built app = `GET /api/artifacts/{id}/download` (used in ota.py:260); list via `/api/apps/{id}/artifacts`.

### A2 Followed projects model
- Tables `project` (upstream_id, package, display_name, auto_push, signer_sha256/since/alert) + `upstream` (repo, channel, asset_regex, latest_tag, downloaded_tag) (`schema/005_follow.sql`). `project.package` is NULL until the first verified download (`projects.py:230-231`).
- `GET /api/projects` = `projects._obj` (`projects.py:273-302`): id, upstream_id, repo, label, display_name, package, downloaded_tag, latest_tag, signer fields, versions[{tag,current,files[{name,abi,size,version_name,version_code}]}], item_ids, device_ids. No icon, no category.
- APK download: `GET /api/projects/{id}/apks/{file}` (`projects.py:483-495, 499`); 409 when signer alert is set. File choice per ABI: `projects.project_info` / `pick_apk` (`projects.py:130-144, 80-86`).
- Install to phone: web `projects.js:193-196` -> `POST /api/projects/{id}/install` {device_ids} (`ota.py:724-745`) -> one manual `ota` job per ADB phone scoped to project_ids -> `ota.handle` -> `_run_project` -> `adb install -r` (`ota.py:554-585, 524-535`). Only phones ASSIGNED to the project are allowed (`ota.py:737-742`); app-kind phones are refused 409 (`APP_ONLY`); phones with no assignment: 400. Android companion devices get projects through the plan (`/api/me/plan`, `ota.py:297-311`).
- ADB path lives in `devices.adb` (`devices.py:102-113`).

### A3 Clients of `GET /api/apps`
- Web: `apps.js:98` (list; uses id, name, package, app_version, version_mode, bundle.*, status.state/progress/fail_*/job_id, download.state/candidates/error; `row()` at 70-84, `filterOf` 40-47); `app.js:254-267` (nav badge `data-apps-count` = `apps.apps.length`, aria "enabled apps"); `devices.js:188-189` (item labels by id, for assignment checkboxes); `settings.js:160`; `projects.js:121` (usedBy list via item ids); `builds.js:54` (maps job target item_id -> app name).
- Android: `RelumeApi.kt:77` -> `AppObject` (`Models.kt:332-343`: id, package, name, icon_url, bundle, app_version, status{state,...}, download, category). Consumers: `AppModel.kt:175` (`apps`), `AppsScreen.kt:52` (list), `RelumeRoot.kt:142` (NavBar badge = `model.apps.size`), `AppModel.kt:393` (watches building/queued job ids), `AppModel.facts`. Kotlin AppObject has NO `version_mode`, `enabled`; `bundle` and `status` and `download` are REQUIRED (non-null), so a followed entry without them would fail kotlinx parse unless AppObject is changed or the entry fills them. Ignore-unknown-keys setting should be checked in RelumeApi (not verified).

### A4 Options and what breaks
Option 1 (recommended): extend `list_enabled`/`market.list_apps` (`apps.py:83`, `market.py:109`) to append one entry per `project` row with `source: 'followed'` (and `source: 'built'` on existing entries). Followed entry fields: `id` collides with item ids (different table) -> use a distinct key such as `project_id` plus a string/namespaced `id`, or keep `id` = project id and rely on `source` (see breakage). Provide `name`=label, `package` (may be null before first download), `icon_url` (icon route is package-based; for null package there is none), `app_version`=downloaded_tag, a `download` URL = first/ABI-matching apk file URL `/api/projects/{id}/apks/{file}`, and `install` device targets from `projects.project_devices`. Bundle/status required by Kotlin AppObject would need nullable or a separate DTO.
Option 2: a separate `followed` array in the same response (`{apps:[...], followed:[...]}`); old consumers unaffected, each client merges for display only. Least risk to counts/jobs.

Existing Download/Install: Followed page already has both (`projects.js:99-100, 193-196`); the Apps page `data-act="dl"` download menu is artifact-based (`apps.js:81`) and has no Install; "Install" on built apps is per-device via Devices page `POST /api/devices/{id}/ota`.

Breakage if followed entries are merged into `apps` as-is:
- Nav badge counts (`app.js:262-264`, `RelumeRoot.kt:142`) would include followed apps and the aria text says "enabled apps".
- Summary counts/filters (`apps.js:87-94`): followed entries have no status.state -> `filterOf` reads `a.status.state` and throws if status absent.
- Item-id keyed code: `devices.js:189` D.items (assignment checkboxes would list followed under items and send project ids as item_ids -> 400 "unknown item"); `settings.js:160`; `builds.js:54` name lookup by item id; `apps.js:107` `appOf` by id; check/build/locate/settings routes on `/api/apps/{id}` would hit a wrong or missing item. Schedules: update schedule runs over `item.enabled` rows only (`apps.py:464`), unaffected unless it were rewritten. Build jobs are item-keyed (`build.item_status`), unaffected.
- Android: `AppModel.kt:393` reads `status.jobId`; `AppsScreen`/`facts` assume status/bundle.
- Tests asserting the exact `/api/apps` shape: `test_apps.py` (app object category test at :255), `test_market_api.py`.

## Part B: Devices page "pending" never clears

The user-visible "pending" is the per-app/project badge on a device card (`devices.js:36, 54-56, 70`, label from `pending` field), also the count in "OTA now · N pending" (`devices.js:46`). "Pair devices" in the report most likely means this Paired devices section. Pairing itself (`devices.pair`, `devices.py:327-372`; web `devices.js:150-157`) has no pending state: it returns the new device and the page reloads.

### B1 Flow
`GET /api/devices` -> `ota.list_devices` (`ota.py:209-212`) -> `_device_view` (`ota.py:171-206`): `pending` = item/project has an `install` step in the plan, planned against `device_packages` (the phone's last report) (`ota.py:172-175`).
Plan rules (`ota.py:105-148`): item is `install` when package not in report ("missing on phone"), or last recorded build_job_id != latest artifact, or "changed on phone" = reported version_code != recorded version_code (`_drifted`, `ota.py:99-102, 138-139`). Project: install when package not in report (`ota.py:92-93`) or tag differs.
ADB OTA job (`ota.handle`, `ota.py:627-678`): `_present(dev)` runs `pm list packages` BEFORE the steps and stores the report (`ota.py:215-226, 665`), then installs, then `_record` writes `ota_installs` only (`ota.py:511-521`).

### B2 Root cause (code-derived)
After a successful ADB install nothing refreshes `device_packages`. The report is a snapshot from before the install, but `ota_installs` already holds the new install.
- Newly installed package: still absent from the report -> `has(pkg)` false -> "missing on phone" -> pending forever (`ota.py:134-135`, project `ota.py:92`).
- Updated package: report keeps the old versionCode, `ota_installs` now holds the new one -> `_drifted` true -> "changed on phone" -> pending forever (`ota.py:138-139`).
Pending only clears at the start of the NEXT OTA job (`_present` refresh), and that job's own result shows pending again after install. The Relume-app path does not have this bug: `post_install` writes the installed package into `device_packages` in the same transaction as the receipt (`ota.py:453-455`). `after_download`/after-build jobs and `install_project` all go through the same `ota.handle`, so they share the bug.
Also: a never-reported ADB row has `present=None` (unknown, `ota.py:173-174`) so it shows pending by build id only until the first OTA; the first OTA then stores a pre-install snapshot, which triggers the stuck state above.
The web client is not at fault: it refetches after every action (`devices.js:140-141`) and every 15 s (`devices.js:200`); the data it gets is wrong.

Suggested fix location (cause, not display): in the ADB install path, after each successful `_install` (`ota.py:554-585, 587-607`, or once at the end of `handle` re-running `_present`) update `device_packages` the same way `post_install` does (`ota.py:453-455`): INSERT OR REPLACE package with the installed versionCode/version_name/signer. Re-running `_present` after the steps is the one-call option; it costs one more adb shell and keeps a single source of truth. (Planner note: `store_reported` keeps only tracked packages, `ota.py:59-69`; installed package is tracked via item/project/artifacts.)

### B3 Confidence and confirming evidence
Confidence: high on the mechanism from code (both branches traced, symmetric app path confirms intent); medium that this is exactly the user's "pending" (other candidates: item with no artifact on the phone ABI -> `NO_MATCHING_ARTIFACT` is action `error`, not pending; project `signer_alert` is error, not pending).
Live check NOT possible: `GET localhost:65427/api/health` works (version 0.0.4) but `/api/devices` returns 401 "login required"; no credentials used. Confirm with: after one OTA on an ADB phone, compare `device_packages` row (version_code) vs newest `ota_installs` row (version_code) for the same package in `webui.db` (read-only sqlite), or `GET /api/devices` logged in: `assignments[].pending` true and `installed_reported` older than `installed`. A red-first test: run `ota.handle` with fake adb for an update, then assert `_device_view` pending false.

## Part C: Existing tests (`tests/`)
Backend-logic (keep): test_apps.py (job input, needs_build, enable/update-run, patches validation; app object category :255), test_market_api.py (5), test_projects.py (51; list shape :377, route/API, signer logic), test_device_ota.py (planner + device view incl. `pending`/`installed_reported` at :175-231), test_device_otajob.py (30; handle/install records, no assertion on device_packages after install = the gap), test_device_app.py (5; app sync/receipts; device_packages check at :40), test_device_routes.py (21; pair/connect/assign routes), test_device_adb.py, test_device_mdns.py, test_device_notify.py, test_build_service.py, test_catalog_*.py, test_core_*.py.
UI-type (candidates for deletion in changed areas): test_ui_static.py (12: nav/logout, manifest, ids exist in html, projects page uses project API, no dialog min-width, builds page leftovers, icon/asset checks). `test_projects_page_uses_project_api` (:65) and `test_every_id_a_page_script_looks_up_exists_in_its_html` (:82) touch pages this batch edits. Android: `MarketParseTest.kt` (API parse) is logic-type.
No test covers `GET /api/apps` returning followed entries or the post-install report refresh.

## Uncertain
- Whether "Pair devices" means the Paired devices card pending badge (assumed) or something in the pairing dialog; pairing code has no pending state.
- Live DB/API state unverified (login required).
- Whether Android's Json config ignores unknown keys (not checked in RelumeApi).
