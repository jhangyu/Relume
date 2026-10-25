# Execution plan — Android companion app and mobile web

Spec (single source of behavior): `docs/superpowers/specs/2026-10-25-android-companion-design.md` (cited §n).
Base: main @ 85d44ea. 10 slices in 2 rounds of 5; round 3 is the fix round.

## 0. Contract (draft for the lead to freeze)

**End state.** The spec's server, app, mobile-web and test-entry changes are on main, both test gates pass within
90 s, and the round reviewers report zero blockers; the first app release and the on-device checks (spec §13) are
then the user's steps.

**In scope.**
1. Server: migration 008, device tokens, `/api/me/*`, Range downloads, followed-project APK route, per-item update
   route, ADB exclusion of app devices, health `companion_version_code` (spec §3–§7).
2. Android app under `android/` (spec §8–§11.1).
3. Mobile web phone layer + PWA manifest (spec §14).
4. `scripts/test.py`, `.github/workflows/android.yml`, project `CLAUDE.md`, `.gitignore`, README sections (spec §11.2, §12).
5. `tests/test_core_jobs.py` sleep rewrite (spec §12.4).

**Out of scope.** Spec §1 out-of-scope list; spec §16 parking lot; the first companion release and GitHub secrets
(user, decision 11); on-device checks (spec §13, user with the lead after merge); release of a new server image.

**Acceptance (all mechanical; run on main after round 2 or 3).**
1. Linux gate: `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell` → exit 0; output has `skipped:` lines and wall time ≤ 90 s.
2. Android gate: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` → exit 0 (or, if the user keeps the toolchain off this Mac, a green `android.yml` run of the same commit: see §4 item 1).
3. Every required test method in §2 exists: `grep -n "def <name>" <file>` (Python) / `grep -n "fun <name>" <file>` (Kotlin) → one hit each, and it ran in gate 1 or 2 (test id present in the gate output or JUnit XML).
4. `grep -rnE "SDK_INT *[<>=!]" android/app/src/main | wc -l` → `2`.
5. `grep -n "sleep" local/webui/tests/test_core_jobs.py` → no hit (exit 1).
6. Local boot smoke (scratch, gate-runner; commands in §3): manifest served without cookie as `application/manifest+json`; `POST /api/auth/device` → 200 with `token`; `POST /api/me/sync` with that token → 200 with `plan.revision`; cookie on `/api/me` → 403; artifact download with `Range: bytes=0-9` → 206 and 10 bytes.
7. `ruby -ryaml -e 'YAML.load_file(".github/workflows/android.yml")'` → exit 0 (macOS ships Ruby with YAML; checked 2026-10-25), and `grep -n "relume-companion" .github/workflows/android.yml` → hits for the repo and the asset name.
8. Round reviewer (fresh, opus): 0 blockers for the round's commits.

**Round budget.** 3 rounds (round 1, round 2, round 3 = fixes only). Budget spent with acceptance open → stop and
report the failure trail to the user.

## 1. Rounds

| Round | Slice | Name | Model | Scope (one line) |
|---|---|---|---|---|
| 1 | A | Server core, auth, migration | opus | migration 008, bearer auth, token issue/logout/revoke, `SESSION_ONLY`, Range `FileResponse`, PWA static types |
| 1 | B | Server downloads and checks | sonnet | artifact download headers, project APK route + sha256, `companion_info`, `item_update_jobs` + `POST /api/items/{id}/update`, auto-push ADB filter |
| 1 | C | Job tests without sleep | sonnet | rewrite `test_core_jobs.py` to state signals (spec §12.4) |
| 1 | D | Android skeleton and API layer | opus | Gradle project, manifest, resources, `RelumeApp`, `api/` (HTTP, models, `ServerUrl`, token store, SSE parser) + tests |
| 1 | E | Mobile web shell and PWA | sonnet | manifest + icons, `app.css`/`app.js` phone layer and overflow menu, index/item/login pages |
| 2 | F | Server devices, OTA, app endpoints | opus | `/api/me/*` (except logout), planner dict `present` + rule 4, companion step, ADB exclusion, `device_packages`, health field |
| 2 | G | Test entry, CI, docs | sonnet | `scripts/test.py`, `android.yml`, project `CLAUDE.md`, `.gitignore`, README |
| 2 | H | Android install, inventory, background | opus | install run, downloader, verifier, PackageInstaller session + receiver, inventory, `CheckWorker`, notifications + tests |
| 2 | I | Android UI | sonnet | Fluent theme, navigation, all screens, `MainActivity` |
| 2 | J | Mobile web pages | sonnet | builds, schedules, devices, settings, projects pages + `test_ui_static.py` |

Cross-slice dependencies (all point to an earlier round or to an ordered commit named here):
- B → A (same round, ordered): A's **first commit** is `server.FileResponse` Range/ETag/headers + `ASSET`/`CTYPES`;
  A's **second commit** is `schema/008_android.sql`. B starts its download and `kind='adb'` filter work after the
  respective commit; B's other work does not wait.
- E → B (same round, ordered): B's **first commit** is `upstream.item_update_jobs` + `POST /api/items/{id}/update`;
  E switches the index buttons after it. E's PWA serving check needs A's first commit.
- C: independent.
- D: independent (codes against spec §5).
- F → A, B (round 1). Scheduler calls `upstream.item_update_jobs` (B).
- G → D (round 1: Gradle wrapper exists). G's **first commit** is `scripts/test.py`; F, H, I, J may use it after.
- H → D (round 1: `api/` models, manifest, dependencies).
- I → D (round 1); I → H (same round, ordered): H's **first commit** is the public entry points of §5 with their real
  implementation; I's wiring commit comes after it. I builds screens against D's `api/` before that.
- J → E (round 1: phone layer, overflow menu); J → F (same round, ordered): devices/settings live check after F's
  device JSON commit. J codes against spec §5 before that.

## 2. Slices

Every slice prompt must include: spec path; this plan's slice section; the shared red lines (§3); the report contract.
Owner blocks list every path the slice may write. A directory entry (ending in `/`) owns every file below it.

### R1-A Server core, auth, migration (opus)
```owner
local/webui/server.py
local/webui/auth.py
local/webui/db.py
local/webui/__main__.py
local/webui/schema/008_android.sql
local/webui/tests/__init__.py
local/webui/tests/helpers.py
local/webui/tests/test_core_auth.py
local/webui/tests/test_core_router.py
local/webui/tests/test_core_db.py
local/webui/tests/test_core_sse.py
local/webui/tests/test_core_startup.py
```
Inputs: spec §3, §4, §5 (rows owned by auth/server), §6.5, §14.1 (`ASSET`, `CTYPES` only), §12.1 items 1–5.
Work: migration 008 exactly as §3 (defaults included); `auth.authenticate`, `_check_credentials`, `SESSION_ONLY`,
`/api/me` token-only rule, `POST /api/auth/device`, `POST /api/me/logout`, `DELETE /api/devices/{id}/token`;
`FileResponse` Range/If-Range/ETag/headers; static `png`/`webmanifest`.
Required tests: `test_core_auth.py`: `test_bearer_token_paths`, `test_session_only_routes_reject_token`,
`test_signer_accept_allows_token`, `test_device_registration_shares_lockout`, `test_bad_bearer_does_not_count_lockout`,
`test_reset_and_password_change_keep_device_tokens`; `test_core_router.py`: `test_range_requests`,
`test_if_range_mismatch_sends_full_body`, `test_static_png_and_manifest_types`; `test_core_db.py`:
`test_migration_008_devices_and_history`.
Acceptance: `cd local && python3 -m unittest webui.tests.test_core_auth webui.tests.test_core_router webui.tests.test_core_db webui.tests.test_core_sse webui.tests.test_core_startup webui.tests.test_device_routes webui.tests.test_device_otajob` → OK (the two device modules prove the migration defaults keep existing INSERTs valid); required tests present.
Red lines: no edit to `ota.py`, `devices.py`, `projects.py` (round 2 / slice B own them); no new migration file other than 008.

### R1-B Server downloads and checks (sonnet)
```owner
local/webui/build.py
local/webui/projects.py
local/webui/upstream.py
local/webui/catalog.py
local/webui/ci.py
local/webui/catalog-seed.json
local/webui/tests/catalog_rows.py
local/webui/tests/test_build_service.py
local/webui/tests/test_build_zip.py
local/webui/tests/test_catalog_inspect.py
local/webui/tests/test_catalog_seed.py
local/webui/tests/test_catalog_store.py
local/webui/tests/test_catalog_upstream.py
local/webui/tests/test_catalog_xposed.py
local/webui/tests/test_projects.py
local/webui/tests/test_ci.py
local/webui/tests/fixtures/build/
local/webui/tests/fixtures/catalog/
local/webui/tests/fixtures/ci/
```
Inputs: spec §5 (download, project APK, item update rows), §6.5 (header names), §6.6, §6.7, §6.2 row
`projects.project_devices`, §12.1 items 6–7.
Work, commit order: (1) `item_update_jobs` + route; (2) after A's first commit: `download_artifact` headers + etag,
`download_apk`; (3) `verify_release` sha256, `_apks` rule, `project_info` fields, `COMPANION_PACKAGE`,
`companion_info`; (4) after A's migration commit: `project_devices` ADB filter.
Required tests: `test_projects.py`: `test_apk_route_membership_and_traversal`, `test_apk_route_signer_alert_409`,
`test_verify_release_writes_sha256`, `test_apks_without_sha256_unreadable`, `test_companion_info`;
`test_catalog_upstream.py`: `test_item_update_jobs_matches_scheduler_update`; `test_build_service.py`:
`test_artifact_download_headers`.
Acceptance: `cd local && python3 -m unittest webui.tests.test_projects webui.tests.test_catalog_upstream webui.tests.test_build_service webui.tests.test_build_zip webui.tests.test_catalog_store webui.tests.test_core_scheduler` → OK; required tests present.
Red lines: `scheduler.py` is not edited (slice F moves the scheduler onto `item_update_jobs`); no `companion.py`.

### R1-C Job tests without sleep (sonnet)
```owner
local/webui/tests/test_core_jobs.py
local/webui/jobs.py
```
Inputs: spec §12.4; `jobs.py:128-262, 347-375`.
Work: rewrite as spec §12.4. `jobs.py` changes only if a needed state signal does not exist; none is expected.
Acceptance: `grep -n "sleep" local/webui/tests/test_core_jobs.py` → no hit; Linux run
`docker run --rm -v "$PWD:/repo" -w /repo/local relume-gate env -u RELUME_VERSION python3 -m unittest -v webui.tests.test_core_jobs`
→ OK with 0 skipped (pidfd exists on Linux) and every test < 1 s (lead's gate-runner runs it; the worker may run it
on macOS where the three pidfd tests show as skipped). Same test count as before.
Red lines: no assertion removed or weakened; no retry loops; no `time.monotonic()` loops.

### R1-D Android skeleton and API layer (opus)
```owner
android/settings.gradle.kts
android/gradle.properties
android/gradlew
android/gradlew.bat
android/gradle/
android/.gitignore
android/app/build.gradle.kts
android/app/src/main/AndroidManifest.xml
android/app/src/main/res/
android/app/src/main/kotlin/io/github/jhangyu/relume/RelumeApp.kt
android/app/src/main/kotlin/io/github/jhangyu/relume/api/
android/app/src/test/kotlin/io/github/jhangyu/relume/api/
```
Inputs: spec §5 (all rows the app calls), §5.1, §8 (all), §10 (screen → call table), §11.1, §12.2 api part.
Work: wrapper 9.8.2 with `distributionSha256Sum` (generate with a downloaded Gradle 9.8.2 distribution in
`.claude/tmp/`, not committed); manifest with every permission and component of spec §8.3; network security config;
launcher icon; `RelumeApp` holds `api`, `prefs`, `tokenStore`; `api/`: `ServerUrl`, `RelumeApi` (one method per
§10 call), models with kotlinx.serialization (missing field = error naming it), `ApiError(status, message, path)`,
`TokenStore` (Keystore AES-GCM), `Prefs`, `SseParser`. Public names in §5 of this plan are binding.
Required tests (`android/app/src/test/kotlin/io/github/jhangyu/relume/api/`): `PlanParseTest.missingFieldNamesField`,
`PlanParseTest.unknownActionFails`, `ServerUrlTest.cleartextRangeEdges`, `ServerUrlTest.rejectsPathQueryUserinfo`,
`SseParserTest.parsesIdDataEndAndComments`.
Acceptance: `cd android && ANDROID_HOME=/opt/homebrew/share/android-commandlinetools ./gradlew testDebugUnitTest --console=plain` → BUILD SUCCESSFUL, the 5 tests in `app/build/test-results/testDebugUnitTest/*.xml`, 0 failures. Toolchain note: this Mac lacks SDK platform 37 and Gradle; if the user has not allowed the downloads (§4 item 1), report BLOCKED with the exact missing component, do not install.
Red lines: no `SDK_INT` comparison in this slice; no `ui/`, `install/`, `inventory/`, `sync/` files; no keystore file.

### R1-E Mobile web shell and PWA (sonnet)
```owner
local/webui/static/app.css
local/webui/static/app.js
local/webui/static/manifest.webmanifest
local/webui/static/icon-192.png
local/webui/static/icon-512.png
local/webui/static/icon-maskable-512.png
local/webui/static/index.html
local/webui/static/index.js
local/webui/static/item.html
local/webui/static/item.js
local/webui/static/login.html
local/webui/static/login.js
```
Inputs: spec §14.1, §14.2, §14.3 rows index/item/login; design tokens `static/app.css:1-3` (icons: emerald accent
`hsl(160 84% 39%)` mark on `#ffffff`, maskable variant with 20 % safe padding; generated with a stdlib script in
`.claude/tmp/`, not committed).
Required checks: `grep -c 'rel="manifest"' local/webui/static/{index,item,login}.html` → 1 each;
`grep -n "min-width:3[0-9][0-9]px" local/webui/static/index.html local/webui/static/item.html` → no hit;
`grep -n "upstreams/\${" local/webui/static/index.js` → no hit (per-item update uses `/api/items/{id}/update`).
Acceptance: the greps above; `cd local && python3 -m unittest webui.tests.test_ui_static` → OK (existing rules);
`python3 -c "import json;json.load(open('local/webui/static/manifest.webmanifest'))"` → exit 0; PNG sizes via
`sips -g pixelWidth -g pixelHeight` = 192/512/512. Portrait check at 360/412 px is a one-off by the reviewer (scratch).
Red lines: no edit to builds/schedules/devices/settings/projects pages (slice J); no service worker.

### R2-F Server devices, OTA, app endpoints (opus)
```owner
local/webui/devices.py
local/webui/ota.py
local/webui/scheduler.py
local/webui/settings.py
local/webui/notify.py
local/webui/events.py
local/webui/tests/device_helpers.py
local/webui/tests/test_device_adb.py
local/webui/tests/test_device_mdns.py
local/webui/tests/test_device_routes.py
local/webui/tests/test_device_ota.py
local/webui/tests/test_device_otajob.py
local/webui/tests/test_device_notify.py
local/webui/tests/test_device_app.py
local/webui/tests/test_core_scheduler.py
local/webui/tests/test_core_settings.py
local/webui/tests/fixtures/device/
```
Inputs: spec §5 (ota/settings rows), §5.1, §6.1–§6.4, §6.8, §12.1 items 8–11.
Work: `/api/me`, `/api/me/sync`, `/api/me/plan`, `/api/me/installs`, `/api/me/adopt` in `ota.py`; planner `present`
dict + rule 4; flags; companion step; `NOT_DOWNLOADED` pull; `last_installs` on `at`; `_present` with
`--show-versioncode` + `device_packages` write; `GET /api/devices` additions; §6.2 table rows in `devices.py`,
`ota.py`, `scheduler.py`; `devices.APP_ERROR_CODES`; scheduler `_app_jobs` update branch calls
`upstream.item_update_jobs`; health field.
Required tests: `test_device_ota.py`: `test_plan_changed_on_phone`, `test_plan_signer_conflict_and_downgrade_flags`,
`test_companion_step_last_and_gated`, `test_not_downloaded_enqueues_one_pull`; `test_device_otajob.py`:
`test_app_devices_excluded_from_adb_fanout`; `test_device_app.py`: `test_sync_stores_tracked_only`,
`test_sync_rejects_malformed_entry`, `test_adopt_keeps_history_and_one_row`, `test_install_receipt_stale_and_valid`;
`test_core_settings.py`: `test_health_companion_version_code`.
Acceptance: after G's first commit, Linux gate `... scripts/test.py --legs python` → exit 0 (whole Python suite: F
touches the shared planner); required tests present.
Red lines: `server.py`, `auth.py`, `projects.py`, `upstream.py` not edited (round-1 owners; request changes through
the lead); no new polling.

### R2-G Test entry, CI, docs (sonnet)
```owner
scripts/test.py
.github/workflows/android.yml
.gitignore
README.md
CLAUDE.md
```
Inputs: spec §11.2, §11.3, §12.3, §12.5.
Work, commit order: (1) `scripts/test.py`; (2) `android.yml`; (3) no keystore change (signing uses the committed `src/keystore/lspatch-signing.p12`);
README sections "Android app" and "Tests"; project `CLAUDE.md`.
Required checks: `grep -n "TOTAL_BUDGET_S = 90" scripts/test.py`, `grep -n "PER_TEST_BUDGET_S = 1.0" scripts/test.py`.
Acceptance (mechanical red-then-green on scratch copies, not committed): a scratch test file with `time.sleep` under a
copy of the tests dir makes `scripts/test.py --legs python` exit 1 naming file:line; a scratch test taking > 1 s
makes it exit 1 listing it; a missing `ANDROID_HOME` with `--legs android` exits 1 naming it; then the real tree:
Linux gate (contract acceptance 1) → exit 0. YAML check (contract acceptance 7) → exit 0.
Red lines: `test.py` uses stdlib only; no per-OS branches except the `gradlew.bat` launcher name; no retry flags.

### R2-H Android install, inventory, background (opus)
```owner
android/app/src/main/kotlin/io/github/jhangyu/relume/install/
android/app/src/main/kotlin/io/github/jhangyu/relume/inventory/
android/app/src/main/kotlin/io/github/jhangyu/relume/sync/
android/app/src/test/kotlin/io/github/jhangyu/relume/install/
android/app/src/test/kotlin/io/github/jhangyu/relume/sync/
```
Inputs: spec §8.1–§8.3, §8.7, §9 (all), §12.2 install/sync parts, §5 of this plan (public entry points).
Required tests: `InstallRunTest.dependencyFailurePropagatesToDependentsOnly`, `InstallRunTest.cancelBlocksDependents`,
`InstallRunTest.serverErrorStepsAreSkipped`, `InstallResultTest.mapsEveryStatus`,
`DownloadDecisionTest.rangeResponses`, `ApkVerifierTest.hexCompareAndMismatchCodes`,
`BackgroundStartTest.startsOnlyOnNewRevisionWithoutActiveRun`.
Acceptance: android gate (contract acceptance 2) → exit 0 with these tests in the JUnit XML;
`grep -rnE "SDK_INT *[<>=!]" android/app/src/main/kotlin/io/github/jhangyu/relume/install` → exactly 1 hit
(`Installer.kt`, API 31).
Red lines: no `setForeground`, no `setPackageSource`, no `getInstallSourceInfo`, no `setRequestUpdateOwnership`, no
sleep/delay/polling; manifest/build file changes go to the lead (slice D owns them, round over → round-3 fix).

### R2-I Android UI (sonnet)
```owner
android/app/src/main/kotlin/io/github/jhangyu/relume/ui/
```
Inputs: spec §8.1 branch 2, §10, §7.4, mockup `docs/superpowers/mockups/android-app/fluent-a.html` + `fluent-alt-NOTES.md`,
§5 of this plan.
Acceptance: android gate → BUILD SUCCESSFUL (compiles with H); `grep -rnE "SDK_INT *[<>=!]" android/app/src/main/kotlin/io/github/jhangyu/relume/ui` → exactly 1 hit (API 33 notification permission);
`grep -rn "MainActivity" android/app/src/main/AndroidManifest.xml` names `.ui.MainActivity`. No UI tests (policy).
Visual check against the mockup frames is a one-off on an emulator or phone (scratch) by the reviewer.
Red lines: no Navigation library; no polling refresh; no new dependency.

### R2-J Mobile web pages (sonnet)
```owner
local/webui/static/builds.html
local/webui/static/builds.js
local/webui/static/schedules.html
local/webui/static/schedules.js
local/webui/static/devices.html
local/webui/static/devices.js
local/webui/static/settings.html
local/webui/static/settings.js
local/webui/static/projects.html
local/webui/static/projects.js
local/webui/tests/test_ui_static.py
```
Inputs: spec §14.1 head tags, §14.3 rows builds/schedules/devices/settings/projects, `test_ui_static.py` additions;
E's overflow menu in `app.js`.
Required tests: `test_ui_static.py`: `test_every_page_links_manifest`, `test_no_dialog_form_min_width`.
Acceptance: `cd local && python3 -m unittest webui.tests.test_ui_static` → OK; `grep -c 'rel="manifest"' local/webui/static/*.html` → 1 per page (8 pages).
Red lines: no edit to E's files (request changes through the lead).

## 3. Shared material for every slice prompt

Red lines (verbatim into each prompt): shared tree git rules — no `git stash` (incl. `stash list`), `reset`,
`checkout --`, `clean`, `add -A`, push; commit only own paths with `git commit -F <msgfile> -- <paths>` (no `;` in
messages); `git rev-parse --abbrev-ref HEAD` = main before commit, `git status --porcelain` clean of own paths after;
write only owned paths plus `.claude/tmp/`; no sleep/polling; no installs on this Mac without the lead's go;
claims verified in code, contradictions with the spec reported, not resolved silently; third-party APIs checked with
context7 against the versions in spec §11.1 before use.

Local boot smoke for contract acceptance 6 (gate-runner, scratch dir under `.claude/tmp/smoke-android/`): start
`cd local && RELUME_DATA=<scratch>/data python3 -m webui` in the Linux image container with port 47000 published
(same image as gate 1), set up the account with `POST /api/auth/setup`, then the curl calls listed in acceptance 6;
log each command, `RC=$?`, status line and the judging header/body line into the scratch log.

## 4. Toolchain and user items

Toolchain check (2026-10-25, this Mac):
```
$ command -v java gradle sdkmanager adb; echo "RC=$?"
/usr/bin/java
/opt/homebrew/bin/sdkmanager
/opt/homebrew/bin/adb
RC=1                      (gradle not found)
$ ls ~/Library/Android/sdk; echo "RC=$?"
(empty directory) RC=0
$ java -version  ->  openjdk version "25.0.2" 2026-01-20 LTS (Temurin, arm64)
SDK actually present: /opt/homebrew/share/android-commandlinetools
  platforms: android-34 android-35 android-36     build-tools: 35.0.0 36.0.0     licenses/: present
ANDROID_HOME / ANDROID_SDK_ROOT: unset
```
AAR metadata (downloaded 2026-10-25): `core-1.19.1` minCompileSdk=37, `lifecycle-viewmodel-compose-android-2.11.0`
minCompileSdk=37, `activity-1.13.0` 36, `work-runtime-2.12.0` 35. SDK repository lists `platforms;android-37.0`.

Items for the user (before round 1 starts, R3: they change what round 1 can verify):
1. **Android toolchain on this Mac.** Slices D, H, I can only run their unit tests here if Gradle 9.8.2 (~130 MB, into
   `~/.gradle`) and SDK platform `android-37.0` (into `/opt/homebrew/share/android-commandlinetools`) are downloaded.
   Options: (a) allow both downloads (Gradle fetches the platform itself because the licenses are already accepted);
   (b) no downloads: Kotlin slices are verified only by `android.yml` on GitHub, which exists from round 2 and needs a
   push of main to `jhangyu/Relume` per check. Recommendation (a).
2. **Companion release repo and fifth secret.** Create the public repo `jhangyu/relume-companion` and the secret
   `RELUME_COMPANION_RELEASE_TOKEN` (fine-grained token, contents write on that repo) in addition to the four keystore
   secrets of decision 11. Needed only for the first release, not for the rounds.
3. After merge: follow `jhangyu/relume-companion` on the Followed projects page (spec §7.1); run the on-device list
   (spec §13).

## 5. Binding Kotlin entry points between slices

Slice D (`api/`), used by H and I:
- `class ServerUrl` with `companion fun parse(text: String): ServerUrl` (throws `InvalidServerUrl(part: String, message: String)`), `fun resolve(path: String): java.net.URL`.
- `class RelumeApi(url: ServerUrl, token: () -> String?)` methods (suspend, `Dispatchers.IO` inside):
  `health()`, `registerDevice(user, password, device)`, `adopt(deviceId)`, `me()`, `sync(report: SyncReport)`,
  `plan(itemIds: List<Int>?, projectIds: List<Int>?)`, `receipt(r: InstallReceipt)`, `logout()`, `catalog()`,
  `item(id)`, `artifacts(id)`, `setSwitches(id, enabled: Boolean?, autoUpdate: Boolean?)`, `build(id)`,
  `update(id, rebuild: Boolean)`, `checkAll()`, `jobs(kind: String?, status: String?, beforeId: Int?, limit: Int?)`,
  `job(id)`, `projects()`, `acceptSigner(projectId, signerSha256)`, `putAssignments(deviceId, itemIds, projectIds)`,
  `openDownload(path: String, from: Long, ifRange: String?): java.net.HttpURLConnection`,
  `jobLog(id, lastEventId: String?, onEvent: (SseEvent) -> Unit)` (blocking read, returns on `event: end`).
- Models: `InstallPlan(revision, steps)`, `PlanStep` (fields of spec §5.1), `Download`, `SignerChange`, `SyncReport`,
  `InstalledPackage(packageName, versionCode: Long, versionName: String?, signerSha256: String)`, `InstallReceipt`,
  `DeviceInfo`. `ApiError(status: Int, message: String, path: String)`.
- `TokenStore(context)`: `read(): String?`, `write(token: String)`, `clear()`. `Prefs(context)`: `serverUrl`,
  `checkInterval: CheckInterval { OFF, H1, H6, H24 }`, `lastBackgroundRevision`.
- `RelumeApp` exposes `api: RelumeApi?` (null before login), `prefs`, `tokenStore`.

Slice H (`install/`, `inventory/`, `sync/`), used by I:
- `object InstallRunner`: `val state: StateFlow<RunState?>`; `fun start(context, plan: InstallPlan, origin: Origin): StartResult`
  (`Origin { UI, BACKGROUND }`, `StartResult { Started, Busy }`); `fun confirm(context, stepIndex: Int)`;
  `fun uninstallAndRetry(context, stepIndex: Int)`; `fun cancel(context)`; `fun reconcileOnStart(context)`;
  `fun canInstall(context): Boolean`.
- `object AppVisibility { @Volatile var visible: Boolean }`.
- `object Inventory`: `fun read(context, tracked: List<String>): List<InstalledPackage>`; `suspend fun sync(context, api): SyncResult`.
- `object CheckScheduler`: `fun apply(context, interval: CheckInterval)`; `val lastCheck: Flow<CheckOutcome?>`.
- `object Notifications`: `fun ensureChannels(context)`.

## 6. Overlap check

Command (repo root): extracts every ```` ```owner ```` block under a `### R<round>-<slice>` heading of this file,
then reports per round exact duplicates and prefix collisions (a directory entry that contains another slice's path
of the same round), and lists tracked files under `local/webui/` (`*.py`, `tests/`, `static/`) not owned by any slice.
Command (inline, run from repo root on 2026-10-25 against this file):
```
python3 -c "<parse '### (R(\d)-([A-Z])) ...' + '```owner' blocks; per round: Counter duplicates, pairs where a
'/'-terminated path of one slice prefixes a path of another slice; Counter across rounds; git ls-files local/webui
filtered to *.py, tests/, static/ minus owned paths; exit 1 if any duplicate/collision or slice count != 10>"; echo "RC=$?"
```
Output:
```
round 1: slices=['R1-A', 'R1-B', 'R1-C', 'R1-D', 'R1-E'] paths=57 duplicates=[] prefix_collisions=[]
round 2: slices=['R2-F', 'R2-G', 'R2-H', 'R2-I', 'R2-J'] paths=39 duplicates=[] prefix_collisions=[]
paths in two rounds: []
tracked local/webui files not owned: ['local/webui/__init__.py']
slices 10
RC=0
```
`local/webui/__init__.py` is not expected to change; any slice that needs it asks the lead. No path is owned in two
rounds, so a round-3 fix of a file goes to a fresh worker holding that file's round-1 or round-2 owner block.
