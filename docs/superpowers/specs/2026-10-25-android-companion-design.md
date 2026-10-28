# Android companion app and mobile web

Base: main @ 85d44ea. This file is the single source of behavior for the work. It merges and overrides:
`docs/logs/2026-10-25-android-decisions.md` (user decisions, binding), `docs/logs/2026-10-25-android-arch-server.md`,
`docs/logs/2026-10-25-android-arch-app.md`, `docs/logs/2026-10-25-android-layout-plan.md`. Where those files and this
spec differ, this spec wins; every difference is listed in §15 "Resolved conflicts".
Execution plan and file ownership: `docs/superpowers/plans/2026-10-25-android-companion-execution.md`.

Server paths are relative to `local/webui/`. Kotlin paths are relative to
`android/app/src/main/kotlin/io/github/jhangyu/relume/` (tests: `android/app/src/test/kotlin/io/github/jhangyu/relume/`).
"(VoD)" marks a behavior that must be confirmed on a real phone; the list is in §13.

## 1. Goal

1. **Native app** (Kotlin + Jetpack Compose, minSdk 29): log in to the Relume server with a per-device token, manage
   apps (Build/Update switches, check one app, build one app, follow job logs), report the phone's installed versions,
   and download + verify + install builds through Android `PackageInstaller` (no ADB). When a new build is found it
   runs the whole install flow in the background; Android shows its confirmation prompt when it requires one.
2. **Mobile web**: the existing web UI gets a portrait phone layer (360–412 px) and a PWA manifest. The PWA can
   download an APK (the browser hands it to the system installer). HTTPS for PWA install comes from the user's
   existing reverse proxy; no server TLS work.
3. **Wireless ADB OTA stays** for devices without the app. Both install paths share one planner.
4. **Test entry** `scripts/test.py`: one script runs the Python, shell and Android unit tests with a 90 s total
   budget, a 1 s per-test cap, a sleep-word grep and a skip count.

Out of scope: root / Shizuku / device-owner silent install; iOS; WebView wrapper; service worker; server TLS;
Android UI tests and screenshots; a global job-status push stream; per-device notification channels on the server.

## 2. User decisions applied (from `2026-10-25-android-decisions.md`)

| # | Decision | Where applied |
|---|---|---|
| 1 | Mockup `fluent-a.html` (Fluent 2) | §10 |
| 2 | minSdk 29; exactly two version branches: `setRequireUserAction(USER_ACTION_NOT_REQUIRED)` only on API ≥ 31, `POST_NOTIFICATIONS` request only on API ≥ 33 | §8.1, §9.4 |
| 3 | Password reset keeps device tokens; revoke only from Settings page or app logout | §4.4 |
| 4 | Cleartext HTTP only for LAN private ranges, Tailscale (100.64.0.0/10, `*.ts.net`) and localhost | §8.5 |
| 5 | No self-update through the server: the user installs and updates the app by hand from this repo's GitHub release | §11.2 |
| 6 | PWA HTTPS from the user's reverse proxy, no server/code work for TLS | §6 |
| 7 | Followed-project signer accept is also allowed from the app | §4.3, §9.3 |
| 8 | Background: new build found → app runs download, verify, PackageInstaller session in the background; prompt via notification | §9 |
| 9 | Full suite ≤ 90 s, reason written in a project `CLAUDE.md` | §12 |
| 10 | `tests/test_core_jobs.py` sleep rewrite is one round-1 slice | §12.4 |
| 11 | Release is signed with the committed `src/keystore/lspatch-signing.p12`; CI uses no GitHub secret | §11.2, §11.3 |

## 3. Data model — migration `schema/008_android.sql`

Pattern as `005_follow.sql` / `007_app_schedule.sql`: `PRAGMA foreign_keys=OFF; BEGIN; … COMMIT; PRAGMA foreign_keys=ON;`,
table rebuild by copy + rename, forward only. `db.migrate()` sets `user_version=8`.

### 3.1 `devices` (rebuilt)
```
id INTEGER PRIMARY KEY,
name TEXT NOT NULL,
kind TEXT NOT NULL DEFAULT 'adb' CHECK (kind IN ('adb','app')),
serial TEXT UNIQUE, mdns_name TEXT, host TEXT, connect_port INTEGER,      -- NULL for app rows
token_hash TEXT UNIQUE,                                                    -- sha256 hex of the device token, NULL = no token
created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),  -- same format as db.FMT
model TEXT, abi_list TEXT, sdk INTEGER, android_release TEXT,
app_version_code INTEGER,                                                  -- companion version reported by the app
ota_after_build INTEGER NOT NULL DEFAULT 0,
last_seen_at TEXT,
CHECK (kind = 'app' OR (serial IS NOT NULL AND host IS NOT NULL AND connect_port IS NOT NULL)),
CHECK (kind = 'adb' OR (serial IS NULL AND host IS NULL AND connect_port IS NULL AND ota_after_build = 0))
```
Existing rows are copied with `kind='adb'`, `created_at` = migration time. The defaults on `kind` and `created_at`
keep the existing `devices.pair` INSERT (`devices.py:346`) valid without a code change.

### 3.2 `device_packages` (new) — the server's view of what is installed on a phone
```
device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
package TEXT NOT NULL, version_code INTEGER NOT NULL, version_name TEXT, signer_sha256 TEXT,
reported_at TEXT NOT NULL, PRIMARY KEY (device_id, package)
```

### 3.3 `ota_installs` (rebuilt)
`job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE` becomes nullable (NULL = install done by the app). New column
`at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))`, backfilled from `jobs.finished_at`, else
`jobs.created_at`. Index `ota_installs_lookup` re-created. The default keeps `ota._record` (`ota.py:198`) valid.

### 3.4 Followed-project re-download
`UPDATE upstream SET downloaded_tag = NULL WHERE kind = 'project'`. The next pull re-downloads
(`upstream._fetch_row` compares `latest_tag` with `downloaded_tag`, `upstream.py:408`) and `projects.verify_release`
writes the new per-file `sha256` (§7.2). No permanent backfill code.

## 4. Auth

### 4.1 Device token
- Token = `secrets.token_urlsafe(32)`; stored as `devices.token_hash = hashlib.sha256(token).hexdigest()`. Returned
  once. scrypt stays for the password only.
- One token per app device row; the token is the device identity.

### 4.2 Request authentication (`server.Handler._api` + new `auth.authenticate(headers) -> (session, device_id)`)
- `Authorization: Bearer <tok>` present → token path only. Unknown or revoked → 401 `{"error":"invalid device token"}`.
  No fall-through to the cookie. Bad bearer tokens do not count toward the login lockout.
- Else the cookie path as today (`auth.session_token`, `server.py:185`).
- `req.session` and `req.device_id`: exactly one is non-None on an authenticated route.
- Paths `/api/me` and `/api/me/*` are token-only → cookie gets 403 `{"error":"device token required"}`.
- `auth.SESSION_ONLY` (token → 403 `{"error":"session required"}`):
  `POST /api/auth/password`, `POST /api/auth/logout`, `PUT /api/settings`, `PUT /api/notify`,
  `DELETE /api/devices/{id}`, `DELETE /api/devices/{id}/token`.
- Every other existing session route accepts a token too.
- CSRF rule unchanged (`server.py:177-183`): the app sends no `Origin`.

### 4.3 Token scope (decision 7)
A token can call every non-`SESSION_ONLY` route. This explicitly includes
`POST /api/projects/{id}/signer/accept` (followed-project signer acceptance, `projects.py:456`). Nothing else is added.

### 4.4 Issue, logout, revoke, reset
- `POST /api/auth/device` (public): body `{username, password, device{name, model, sdk, android_release, abi_list[]}}`.
  Shares the credential check and lockout with `auth.login`: extract `auth._check_credentials(req)` from `login`
  (`auth.py:137-160`); both routes call it. Success inserts an `app` device row and returns
  `{token, device, adb_devices:[{id, name, model}]}`. No cookie. 409 `setup required` when no account exists.
- `POST /api/me/logout` (token): sets this device's `token_hash = NULL`, returns `{ok:true}`.
- `DELETE /api/devices/{id}/token` (session): `token_hash = NULL`; row, assignments and history stay. 409
  `not an app device` for ADB rows. Deleting the device row also revokes.
- `auth.reset_password` (`auth.py:58-68`) and `auth.change_password` keep deleting cookie sessions only; device tokens
  survive (decision 3).

## 5. API contract

Conventions unchanged (`server.py`): JSON bodies, mutating requests need `Content-Type: application/json` (415),
errors `{"error": msg}`. Auth: P public, S session cookie, T device token, S|T either.

| Method | Path | Auth | Request | Response | Errors | Owner module |
|---|---|---|---|---|---|---|
| POST | /api/auth/device | P | `username, password, device{name, model, sdk, android_release, abi_list[]}` | `{token, device, adb_devices[{id,name,model}]}` | 400 field, 401 `wrong credentials`+`attempts_left`, 429 `locked`+`retry_after`, 409 `setup required` | auth |
| POST | /api/me/logout | T | – | `{ok}` | 401 | auth |
| DELETE | /api/devices/{id}/token | S | – | `{device}` | 404, 409 `not an app device` | auth |
| POST | /api/me/adopt | T | `{device_id}` (an ADB row) | `{device}` | 400, 404, 409 `not an ADB device` | ota |
| GET | /api/me | T | – | `{device, tracked_packages[]}` | 401 | ota |
| POST | /api/me/sync | T | `device{model, sdk, android_release, abi_list[], app_version_code}`, `packages[{package, version_code, version_name, signer_sha256}]` | `{device, tracked_packages[], plan{revision, steps[]}}` | 400 names field and index, 401 | ota |
| GET | /api/me/plan | T | `?items=1,2&projects=3` (optional, ids must be assigned) | `{plan{revision, steps[]}}` | 400 `item not assigned to this device`, 401 | ota |
| POST | /api/me/installs | T | `{kind: item\|project, ref_id, artifact_id\|tag, result: installed\|failed, error_code?, error_text?}` | `{ok}` | 400 field/code, 409 `stale plan` | ota |
| GET | /api/artifacts/{id}/download | S\|T | `Range`, `If-Range` | APK bytes + headers §7.1; 206 + `Content-Range` | 404, 416 | build + server |
| GET | /api/projects/{id}/apks/{file} | S\|T | `Range`, `If-Range` | APK bytes + headers §7.1 | 404 project/file, 409 `signer changed: confirm on the Followed projects page`, 416 | projects + server |
| POST | /api/items/{id}/update | S\|T | `{rebuild?: bool}` (default false) | `{job_ids[]}` | 400 `rebuild must be a boolean`, 404 item | upstream |
| GET | /api/devices | S\|T | – | each device adds `kind`, `created_at`, `last_seen_at`, `app_version_code`; `online` is null for app rows; each assignment and project adds `installed_reported{version_code, version_name, signer_sha256, reported_at}` or null | – | ota |
| PUT | /api/items/{id} | S\|T | `enabled`, `auto_update` | item detail | as today | catalog (unchanged) |
| POST | /api/items/{id}/build | S\|T | – | `{job_id}` | as today | build (unchanged) |
| POST | /api/upstreams/check, /api/upstreams/{id}/pull | S\|T | as today | as today | as today | upstream (unchanged) |
| GET | /api/catalog, /api/items/{id}, /api/items/{id}/artifacts, /api/jobs, /api/jobs/{id}, /api/jobs/{id}/log (SSE), /api/projects | S\|T | as today | as today | as today | unchanged |
| POST | /api/projects/{id}/signer/accept | S\|T | `{signer_sha256}` | project object | as today | projects (unchanged) |
| PUT | /api/devices/{id}/assignments | S\|T | as today | as today | as today | devices (unchanged) |
| GET | /static/manifest.webmanifest, /static/*.png | P | – | manifest JSON / PNG | 404 | server |

`device` object in `/api/me*` responses = the `GET /api/devices` device object of that row.
`tracked_packages` = sorted distinct non-NULL `item.package` ∪ `project.package`.

### 5.1 Plan step object (`/api/me/sync`, `/api/me/plan`)
```
kind: item | project        ref_id: int        package: str        display_name: str
action: install | up_to_date | error    reason: str        needs: [project ids]
signer_conflict: bool    downgrade: bool    pull_job_id: int | null    signer_change: {pinned, offered} | null
download: null | {url, file_name, size, sha256, signer_sha256, version_code, version_name, artifact_id (item) | tag (project)}
```
- Order = `ota.plan()` order (dependency projects before the item that needs them, direct projects after items,
  `ota.py:96-101`).
- `download` is non-null iff `action == install`.
- `signer_conflict`: reported signer present and ≠ `download.signer_sha256`. `downgrade`: reported version_code >
  `download.version_code`. Both false when the package is not reported.
- Project with no downloaded tag (`reason == 'not downloaded'` in `ota._project_action`): the `/api/me` handlers turn it
  into `action error`, `reason NOT_DOWNLOADED`, and set `pull_job_id = jobs.enqueue('upstream', {'upstream_id': u},
  'manual', options={'rebuild': False})` (dedup makes repeats free). Dependent items then fail as dependencies.
- `SIGNER_CHANGED` steps carry `signer_change = {pinned: project.signer_sha256, offered: alert}`; the app sends
  `offered` to `POST /api/projects/{id}/signer/accept`.
- `revision` = sha256 hex of `json.dumps(steps, sort_keys=True, separators=(',',':'))`.
- Receipts: `POST /api/me/installs` key is `download.artifact_id` (item, must be the newest artifact for that item for
  the device's first ABI) or `download.tag` (project, must equal the current downloaded tag), else 409 `stale plan`.
  `error_code` ∈ `devices.ERROR_CODES` values ∪ `{HASH_MISMATCH, SIGNER_MISMATCH_FILE, INSTALLER_ERROR, USER_REJECTED,
  DEPENDENCY_FAILED}` (one list `devices.APP_ERROR_CODES`). User cancel = `failed` + `USER_REJECTED`. A receipt inserts
  one `ota_installs` row (`job_id NULL`, `build_job_id`/`upstream_tag`, package, version, result).

## 6. Server behavior

### 6.1 Planner (one decision function for both transports, `ota.py`)
- `present` becomes `{package: {version_code, version_name, signer_sha256}}` or `None` (unknown, as today).
- `last_installs` reads `ota_installs.at` and `version_code`, no JOIN on `jobs` (app rows have `job_id NULL`).
- Item rule in `plan()`: (1) no artifact → `error NO_MATCHING_ARTIFACT`; (2) package not present → install `missing on
  phone`; (3) no install record or record `build_job_id` ≠ newest artifact `job_id` → install `new build`;
  (4) new: reported `version_code` ≠ recorded install `version_code` → install `changed on phone`; (5) up_to_date.
  Build-job id stays the truth (patched builds keep the upstream versionCode when only patches change); rule 4 is a
  drift check.
- `_present` uses `adb shell pm list packages --show-versioncode`; `devices.parse_packages` returns
  `{package: {version_code, version_name: None, signer_sha256: None}}`; the OTA job writes it into `device_packages`.
- `_device_view` passes `device_packages` rows as `present` and adds `installed_reported`.

### 6.2 App devices excluded from ADB code (one filter in each existing function)
| Place | Rule |
|---|---|
| `devices.resolve_devices` | `{"all":true}` → `kind='adb'` only; explicit ids drop app rows |
| `scheduler._check_target` (ota) | explicit `device_ids` with an app row → 400 `device <id> installs through the Relume app` |
| `ota.on_build_succeeded` | SQL adds `kind='adb'` |
| `projects.project_devices` (used by `enqueue_auto_push`) | `kind='adb'` only |
| `ota.ota_now`, `ota.install_project`, `ota._recovery` | 409 `device installs through the Relume app`; `install_project` default target = ADB rows |
| `ota.handle` | app row → `JobFailed('device installs through the Relume app')` |
| `devices.device_json`, `ota.list_devices` | `online` null for app rows |
| `devices.patch_device` | `ota_after_build` → 400 for app rows |
| `devices.delete_device` | `adb disconnect` only for ADB rows |

### 6.3 Adopt an ADB row
`POST /api/me/adopt {device_id}` (one transaction): target row must be `kind='adb'` (else 409); it becomes
`kind='app'` with ADB columns NULL, `ota_after_build=0`, the caller's `token_hash`, model/sdk/abi facts of the caller;
the caller's just-registered row is deleted. Assignments, `device_projects` and `ota_installs` stay on the adopted id.

### 6.4 Sync
`POST /api/me/sync` parses the body at the boundary: package regex `server.PACKAGE`, ints, `signer_sha256` 64 lowercase
hex or null, `abi_list` values in `catalog.ABIS`; error names field and index. Stores only still-tracked packages in
`device_packages` (replace all rows of the device in one transaction), updates device facts, `app_version_code`,
`last_seen_at`; returns device, tracked packages and the full plan (report + plan in one call).

### 6.5 Downloads (`server.FileResponse`)
`FileResponse(path, download_name=None, cache_seconds=0, etag=None, headers=None)`. `Handler._send` adds
`Accept-Ranges: bytes`, `ETag: "<etag>"` when given, extra headers. `Range: bytes=a-b | a- | -n` (one range) → 206 +
`Content-Range`; multi-range, unparsable or unsatisfiable → 416 + `Content-Range: bytes */<size>`; `If-Range` ≠ ETag →
200 full body. Both download routes pass `etag=sha256` and headers `X-Relume-Sha256`, `X-Relume-Signer-Sha256`,
`X-Relume-Package`, `X-Relume-Version-Code`.

### 6.6 Followed-project APKs (`projects.py`)
- `verify_release` adds `"sha256"` per file (reuse `upstream._sha256`, `upstream.py:336`).
- `_apks` treats a facts list where any entry lacks `sha256` as unreadable (returns None).
- `project_info` adds `sha256`, `size`, `signer_sha256` of the picked APK.
- New `download_apk` route: serves `file` only if listed in the downloaded tag's `apks.json` (membership = no path
  traversal); 409 while a signer alert exists.

### 6.7 Per-item update trigger (`upstream.py`)
- New `upstream.item_update_jobs(item_ids, project_ids, trigger, schedule_id, options) -> [job_id]`: the update branch
  of `scheduler._app_jobs` (`scheduler.py:149-162`) moved verbatim: source patches + CLI upstream + dependency project
  upstreams of the items + upstreams of the projects, one `appcheck` per package of `latest`-mode items. `scheduler`
  keeps its `auto_update` filter and calls this function.
- `POST /api/items/{id}/update {rebuild?}` → `item_update_jobs([id], [], 'manual', None, {'rebuild': rebuild,
  'notify': True})`; no `auto_update` filter (explicit user action).

## 7. Delivery details

### 7.1 Download headers
See §6.5. The app compares `X-Relume-Sha256` and `X-Relume-Signer-Sha256` with the plan; a mismatch = stale plan.

### 7.2 Integrity chain
Server: artifact `sha256`/`signer_sha256` from the build runner (`build.py:98-101`); project `sha256` from
`verify_release`, signer from `apksigner` (pinned, alert on change). App: recomputes both before opening a session (§8.3).

## 8. App — platform rules at minSdk 29

### 8.1 SDK levels and the two approved branches
| Setting | Value | Reason |
|---|---|---|
| minSdk | 29 | decision 2 |
| compileSdk | 37 | `androidx.core:core:1.19.1` and `lifecycle-viewmodel-compose:2.11.0` declare `minCompileSdk=37` in their AAR metadata (checked 2026-10-25) |
| targetSdk | 37 | newest platform, not on Play |

The only `Build.VERSION.SDK_INT` checks in the code base:
1. `if (SDK_INT >= 31) params.setRequireUserAction(SessionParams.USER_ACTION_NOT_REQUIRED)` in `install/Installer.kt`.
2. `if (SDK_INT >= 33)` request `POST_NOTIFICATIONS` in `ui/` Settings and first-run flow.
A mechanical check enforces this: `grep -rnE "SDK_INT *[<>=!]" android/app/src/main` = exactly 2 hits
(`SDK_INT` read as a value for the sync report, §9.6, is not a comparison).

### 8.2 API choices that avoid other version branches
| Need | API used on all versions | Why not the newer API |
|---|---|---|
| installer of record | `PackageManager.getInstallerPackageName(pkg)` (deprecated in 30, works on 29–37) | `getInstallSourceInfo` is API 30 |
| package source | not set | `setPackageSource` is API 33 |
| update ownership | not requested | API 34; would also force a phone prompt for ADB OTA updates |
| PendingIntent mutability | `androidx.core.app.PendingIntentCompat.getBroadcast(..., isMutable = true)` / `getActivity(..., isMutable = false)` | `FLAG_MUTABLE` is API 31; the compat class owns the check |
| archive / installed info | `getPackageArchiveInfo(path, Int)` and `getPackageInfo(pkg, Int)` with `GET_SIGNING_CERTIFICATES` (API 28) | `PackageInfoFlags` overloads are API 33 |
| version code | `PackageInfoCompat.getLongVersionCode` | – |
| notifications enabled | `NotificationManagerCompat.areNotificationsEnabled()` | – |
| dark theme | `isSystemInDarkTheme()` (system dark mode exists from 29) | – |

### 8.3 Install rules
- No prompt happens only when (a) API ≥ 31 and `USER_ACTION_NOT_REQUIRED` set, (b) the installed app's own
  targetSdk meets Android's floor for that OS version, (c) this app is installer of record.
  On API 29/30 every install/update prompts. The code always handles `STATUS_PENDING_USER_ACTION`.
- First install of a package through the app always prompts. Packages installed earlier by ADB need one confirmed
  handover install; the step shows "one confirmation needed" when `getInstallerPackageName(pkg)` ≠ own package.
- Manifest permissions: `INTERNET`, `REQUEST_INSTALL_PACKAGES`, `REQUEST_DELETE_PACKAGES`, `QUERY_ALL_PACKAGES`,
  `POST_NOTIFICATIONS`, `UPDATE_PACKAGES_WITHOUT_USER_ACTION`. Permissions unknown to an older platform are ignored
  by it (no branch).
- Manifest components: application `.RelumeApp` (`allowBackup="false"`, `networkSecurityConfig`), activity
  `.ui.MainActivity` (launcher, exported), receiver `.install.InstallResultReceiver` (`exported="false"`).
  WorkManager uses its default `androidx.startup` initializer. `buildFeatures { buildConfig = true }` (AGP 9 default
  is off; the sync report and Settings read `BuildConfig.VERSION_CODE`).

### 8.4 Package visibility
API 29 has no visibility filtering; API 30+ uses the declared `QUERY_ALL_PACKAGES` (tracked packages come from the
server catalog at runtime, so a build-time `<queries>` list cannot cover them). Lint's `QueryAllPackagesPermission`
warning is suppressed in the manifest with `tools:ignore` and the reason "sideloaded companion, package list is
server-defined" (user is told, per the suppress rule).

### 8.5 Server URL and cleartext (decision 4)
`network_security_config.xml`: `<base-config cleartextTrafficPermitted="true">` with system trust anchors only.
`api/ServerUrl.parse(text)` enforces the real rule: scheme `http`|`https`, host, optional port, no path/query/userinfo.
`http` is accepted only for: `localhost`, `127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`,
`100.64.0.0/10`, IPv6 ULA `fc00::/7`, names ending in `.ts.net`. Everything else over http → error
"use https:// for public addresses". Names are checked by name, never by resolved IP. Errors name the rejected part.

### 8.6 Token storage
AES-256-GCM ciphertext in `SharedPreferences`; key = non-exportable Android Keystore key
(`KeyGenParameterSpec`, `BLOCK_MODE_GCM`, `ENCRYPTION_PADDING_NONE`, 256 bit, `setUserAuthenticationRequired(false)` so
workers can read it while the phone is locked). `android:allowBackup="false"`. Decrypt failure → login screen with the
exception text. `androidx.security:security-crypto` is not used (deprecated).

### 8.7 Background work without a foreground service
Workers never call `setForeground`. A stopped worker (10-minute execution window, process death) is rescheduled by
WorkManager and resumes its download from the `.part` file (§9.2). This removes foreground-service types, the
`FOREGROUND_SERVICE*` permissions, the API 31+ ban on starting foreground services from the background and the
Android 15 data-sync time limit.

## 9. App — install pipeline (one path for UI-started and background runs)

### 9.1 Run model
- `install/InstallRun.kt` (pure Kotlin): steps from `InstallPlan` in server order; per-step state
  `pending | downloading | verifying | committed | awaiting_user | needs_uninstall | installed | up_to_date | failed |
  cancelled | dependency_failed | server_error`. A step that ends `failed`/`cancelled`/`server_error`/`needs_uninstall`
  marks every later step whose `needs[]` contains its `ref_id` (projects) as `dependency_failed`; independent steps
  continue. Server `action error` steps become `server_error` with the server reason and are not run.
- `install/InstallRunStore.kt`: one active run per process, persisted as `filesDir/install-run.json` and exposed as
  `StateFlow<RunState?>`. A new run is refused with "an install run is in progress" while a step is `downloading`,
  `verifying`, `committed` or `awaiting_user`.
- Driver: `install/InstallWorker.kt` (unique work `install-run`, `ExistingWorkPolicy.APPEND_OR_REPLACE`). It advances
  steps until one is committed to `PackageInstaller`, then returns. `install/InstallResultReceiver.kt` (exported=false)
  records the session result and enqueues `InstallWorker` again. Fully event-driven; no waits, no timers.
- Constraints: UI-started runs `NetworkType.CONNECTED`; background runs `NetworkType.UNMETERED` + battery not low.

### 9.2 Download (`install/Downloader.kt`, pure decision in `install/DownloadDecision.kt`)
`HttpURLConnection` streaming to `filesDir/apk/<sha256>.part`. Existing `.part` → `Range: bytes=<len>-` +
`If-Range: <ETag>`. 206 → append; 200 → truncate and write full body; 416 → delete `.part`, restart once, second 416
fails the step; any other status → fail with status and URL. `Content-Length` (200) ≠ plan `size` → fail.

### 9.3 Verify (`install/ApkVerifier.kt`)
1. SHA-256 of the complete file = `download.sha256`; response `X-Relume-Sha256` / `X-Relume-Signer-Sha256` = plan
   values (mismatch → `HASH_MISMATCH`, stale plan).
2. `getPackageArchiveInfo(path, GET_SIGNING_CERTIFICATES)` → SHA-256 of `signingInfo.apkContentsSigners[0].toByteArray()`
   = `download.signer_sha256` (lowercase hex compare), archive `packageName` and long version code = plan
   (mismatch → `SIGNER_MISMATCH_FILE`). (VoD on Android 10: `signingInfo` is non-null for an archive.)
3. Installed package with a different signer, or a higher version code → step `needs_uninstall` (no session opened).
Failed verification deletes the file.

### 9.4 Session and results (`install/Installer.kt`, `install/InstallResult.kt`)
- `SessionParams(MODE_FULL_INSTALL)`, `setAppPackageName`, `setSize`, `setInstallReason(INSTALL_REASON_USER)`, branch 1
  of §8.1. `openWrite` → copy → `fsync` → commit with the `PendingIntentCompat` broadcast to `InstallResultReceiver`.
- `InstallResult.from(status, extras)` maps: `STATUS_PENDING_USER_ACTION` → `awaiting_user`: if
  `install/AppVisibility.visible` (set by `MainActivity.onStart/onStop`) start `EXTRA_INTENT` with
  `FLAG_ACTIVITY_NEW_TASK`, else post notification "Tap to confirm install of <name>" whose content intent wraps
  `EXTRA_INTENT` (VoD); `STATUS_SUCCESS` → `installed`; `STATUS_FAILURE_ABORTED` → `cancelled` (`USER_REJECTED`);
  `STATUS_FAILURE_CONFLICT` → `needs_uninstall`; every other `STATUS_FAILURE*` → `failed` `INSTALLER_ERROR` with the
  constant name + `EXTRA_STATUS_MESSAGE`. No automatic retry.
- Receipts: `InstallWorker` sends `POST /api/me/installs` for each terminal item/project step and
  then `POST /api/me/sync`. 409 `stale plan` → re-sync, show the new plan, no automatic reinstall.
- Process start (`InstallRunner.reconcileOnStart`): abandons own sessions not referenced by the run file; a
  `committed`/`awaiting_user` step whose session is gone becomes `installed` if the installed version code and signer
  equal the target, else `failed INSTALLER_ERROR "session lost"`.
- `needs_uninstall`: dialog with both signers (short hex) or both version codes and "Uninstalling deletes this app's
  data on the phone (logins, downloads, settings). This cannot be undone." → `PackageInstaller.uninstall(
  VersionedPackage, IntentSender)` (system confirmation, VoD) → the step is re-run. In a background run the step
  posts "Open Relume: <name> needs a reinstall" and stops there.
- `REQUEST_INSTALL_PACKAGES` gate: `canRequestPackageInstalls()` false → a background run fails the step with
  `INSTALLER_ERROR "install permission off"` and posts a notification; the UI explains and opens
  `Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES` with `package:` URI and re-checks in `onResume`.

### 9.5 Background check (`sync/CheckWorker.kt`, `sync/CheckScheduler.kt`)
- Unique periodic work `check`, `ExistingPeriodicWorkPolicy.UPDATE`, interval from Settings: `Off | 1 h | 6 h (default)
  | 24 h`; `Off` cancels it. Constraint `NetworkType.CONNECTED`.
- Run: `POST /api/me/sync`; if the plan has `action=install` steps, no run is active, and `plan.revision` ≠ the last
  revision started in the background (`Prefs`) → start a background run (§9.1) and store the revision. Failures return
  `Result.failure()` with code + message shown on Settings "last check"; IO errors return `Result.retry()`
  (WorkManager backoff, the allowed IO case).
- Notification channels (`sync/Notifications.kt`): `install` (confirm prompts, results, needs-reinstall). Branch 2 of
  §8.1 asks `POST_NOTIFICATIONS` on first enable of background checks; denied → Settings shows "notifications off" and
  the Apps screen lists steps `awaiting_user` with a "Confirm" button.

### 9.6 Inventory (`inventory/Inventory.kt`)
For each `tracked_packages` entry: `getPackageInfo(pkg, GET_SIGNING_CERTIFICATES)` → version name, long version code,
SHA-256 of signer #1; `NameNotFoundException` = not installed (the API's defined answer, omitted). Device facts:
`Build.MODEL`, `SDK_INT` (as data, not a branch), `Build.VERSION.RELEASE`, `Build.SUPPORTED_ABIS`, `app_version_code`.
Sync triggers: after login, after each install result, on `ON_START`, in `CheckWorker`. No package broadcast receiver.

## 10. App — UI (Fluent 2, `docs/superpowers/mockups/android-app/fluent-a.html`)

- Theme `ui/Theme.kt` = `FluentTheme` CompositionLocal on `compose.foundation`; `material3` only for theme-neutral
  shells (Scaffold, ModalBottomSheet, AlertDialog). Tokens (light / dark): bg `#fafafa`/`#141414`, bg2 `#fff`/`#1f1f1f`,
  card `#fff`/`#292929`, stroke `#e0e0e0`/`#3d3d3d`, stroke2 `#d1d1d1`/`#525252`, fg `#242424`/`#fff`, fg2
  `#424242`/`#d6d6d6`, fg3 `#707070`/`#a3a3a3`, accent `#0e7a56`/`#3dbf8d`, on-accent `#fff`/`#06231a`, ok
  `#107c10`/`#54b054`, warn `#bc4b09`/`#faa06b`, danger `#b10e1c`/`#dc626d`, info `#0f6cbd`/`#479ef5`. Radii 4 / 8 / 12.
  Body 14 sp, title 20 sp semibold, versions monospace.
- Components: pivot tab row with underline, tinted badge, outlined switch 40×22 (row is the 48 dp hit target),
  message bar, install step list, bottom nav with pill indicator, log pane (monospace `LazyColumn`).
- Navigation: no library. `rememberSaveable` back stack of a sealed `Screen` + `BackHandler`; bottom tabs Apps /
  Builds / Settings are stack roots; Login replaces the stack. `ui/MainActivity.kt` calls `enableEdgeToEdge()` and
  `setContent { RelumeRoot() }`, and sets `AppVisibility` in `onStart`/`onStop`.

| Screen (mockup frame) | State | Calls |
|---|---|---|
| Login (1, 1b unreachable, ADB adopt choice) | url, user, password, adb choice, error | `GET /api/health`, `POST /api/auth/device`, `POST /api/me/adopt` |
| Apps list (2 light/dark) | catalog + plan steps by package + active jobs + run state | `GET /api/catalog`, `POST /api/me/sync`, `GET /api/jobs?status=running` and `?status=queued` on load and pull-to-refresh, `POST /api/upstreams/check` |
| App detail (3) | item, artifacts, reported facts, scoped plan | `GET /api/items/{id}`, `GET /api/items/{id}/artifacts`, `PUT /api/items/{id}` (Build=`enabled`, Update=`auto_update`), `POST /api/items/{id}/build`, `POST /api/items/{id}/update`, `GET /api/me/plan?items={id}`, `PUT /api/devices/{own id}/assignments` when not yet assigned |
| Job log (4) | lines, status | `GET /api/jobs/{id}`, SSE `GET /api/jobs/{id}/log` with `Last-Event-ID` resume |
| Install (5a download, 5b prompt) | `InstallRunner.state` | plan, downloads, receipts |
| Signature / downgrade dialog (5c) | step | uninstall flow §9.4 |
| Dependency dialog (5d) | step `needs` | – |
| Signer change dialog (same component as 5c) | `signer_change` | `POST /api/projects/{id}/signer/accept {signer_sha256: offered}` after "Accepting trusts a new signing key for <name>" confirmation |
| Builds | jobs page | `GET /api/jobs?kind=build&before_id=&limit=` |
| Settings (6) | server, device, check interval, notifications status, last check, about (app version), logout | `GET /api/me`, `POST /api/me/logout` |

Live state without polling: SSE per job the app started or saw active; on `event: end` refresh the item and plan.
Jobs started elsewhere are found on screen load and pull-to-refresh only. Errors show HTTP status + server `error`
text + path. 401 → clear token, login with "This device was signed out by the server". 403 `session required` is shown
as a bug.

## 11. App — build, signing, CI

### 11.1 Gradle (`android/`)
Single module `:app`, no version catalog. Versions (registry-checked 2026-10-25, re-check with context7 at
implementation): Gradle wrapper 9.8.2 (with `distributionSha256Sum`), AGP 9.4.1, Kotlin + Compose compiler plugin +
serialization plugin 2.4.21, Compose BOM 2026.09.00 (ui 1.12.1, material3 1.4.0), `androidx.core:core-ktx` 1.19.1,
`androidx.activity:activity-compose` 1.13.0, `androidx.lifecycle:lifecycle-viewmodel-compose` 2.11.0,
`androidx.work:work-runtime-ktx` 2.12.0, `org.jetbrains.kotlinx:kotlinx-serialization-json` 1.12.0, `junit:junit`
4.13.2. HTTP = `HttpURLConnection`. Java/Kotlin bytecode target 17 (no toolchain block: builds with any JDK ≥ 17).
applicationId and namespace `io.github.jhangyu.relume`. Every build type: versionName `X.Y.Z` and versionCode `X*1000000+Y*1000+Z` from the nearest
`companion-vX.Y.Z` git tag reachable from HEAD (no tag or a malformed tag fails the build); signing with
`src/keystore/lspatch-signing.p12` (public credentials as named constants in `build.gradle.kts`). `android/.gitignore`:
`.gradle/`, `build/`, `app/build/`, `local.properties`.

### 11.2 CI `.github/workflows/android.yml`
- Trigger: `push` of a tag `companion-vMAJOR.MINOR.PATCH` only. CI never runs tests (`scripts/ci_guard.py` fails any
  workflow that does). Version name = `MAJOR.MINOR.PATCH`, version code = `MAJOR*1000000 + MINOR*1000 + PATCH`
  (monotonic; the plan's `downgrade` flag compares version codes).
- One job on `ubuntu-latest`, `permissions: contents: write`: checkout → guard → derive version from the tag →
  setup-java (temurin 17) → setup-gradle → `android/gradlew -p android assembleRelease --no-watch-fs` → copy
  `app-release.apk` to `relume-companion.apk` → `gh release create "$GITHUB_REF_NAME" relume-companion.apk` on this
  repo with `GH_TOKEN` = the workflow's built-in `github.token`. No secrets.
- Local gate: `.githooks/pre-push` runs `scripts/ci_local.py` (guard + the same Gradle build).

### 11.3 Signing key
The committed `src/keystore/lspatch-signing.p12` (alias `fiorenmas`, public credentials from `src/build/build-item.sh`): personal use, no dedicated key and no signing secrets.

## 12. Tests and test entry

### 12.1 Committed Python tests (core logic / security / data integrity, each ≤ 1 s, stdlib unittest)
1. Bearer: valid → 200; unknown/revoked → 401, no cookie fall-through; cookie on `/api/me/*` → 403; token on each
   `SESSION_ONLY` route → 403; `POST /api/projects/{id}/signer/accept` is not session-only. (test_core_auth)
2. `POST /api/auth/device` shares the lockout counter with login; bad bearer tokens do not count. (test_core_auth)
3. `reset_password` and `change_password` keep `token_hash`. (test_core_auth)
4. Range: `bytes=0-9` 206; `bytes=10-`; `bytes=-5`; multi-range 416; beyond size 416; `If-Range` mismatch → 200. (test_core_router)
5. Migration 008 on a v7 DB with ADB devices + ota_installs: rows `kind='adb'`, `at` backfilled, CHECK rejects an app
   row with host; existing `devices.pair` INSERT shape still accepted; project `downloaded_tag` NULL. (test_core_db)
6. Project APK route: file not in `apks.json` (incl. `..%2F`) 404; signer alert 409; `verify_release` writes sha256;
   `_apks` without sha256 → None. (test_projects)
7. `item_update_jobs`: scheduler update and `POST /api/items/{id}/update` give the same job set. (test_catalog_upstream)
8. `plan()` with a `present` dict: rule 4; `signer_conflict` and `downgrade` flags; dependency-before-item order; `NOT_DOWNLOADED` enqueues one pull. (test_device_ota)
9. App rows excluded from ADB fan-out (`resolve_devices`, `on_build_succeeded`, auto-push, `ota_now` 409,
   `ota.handle` fails) with `FakeAdb` asserting zero adb calls. (test_device_otajob)
10. `/api/me/sync` stores only tracked packages, rejects a malformed entry with a field-naming 400; adopt keeps
    assignments and history on the ADB id and leaves one row; installs receipt stale → 409, valid → next plan
    up_to_date. (test_device_app, new)

### 12.2 Committed Kotlin JVM unit tests (plain JUnit, no Robolectric, no Android types in tested code)
api/: plan parsing (missing field → error naming it; unknown action → error), `ServerUrl` rule at every range edge
(10/8, 172.15 vs 172.16 vs 172.31 vs 172.32, 192.168/16, 100.63/100.64/100.127/100.128, `.ts.net`, `fc00::/7`,
`::1`, public IP and name rejected over http), SSE parser (`id:`, multi-line `data:`, comments, `event: end`).
install/: `InstallRun` transitions (dependency propagation, cancel blocks dependents, server error steps skipped,
order kept), `InstallResult` mapping table for every `STATUS_*`, `DownloadDecision` (206/200/416/other), verifier
pure parts (hex normalization, mismatch codes).
sync/: background-start decision (install steps × run active × revision seen).

### 12.3 `scripts/test.py` (stdlib only, one code path on every OS)
- `python3 scripts/test.py [--legs python,shell,android]`, default all three. A requested leg whose tool is missing
  (`bash`; `ANDROID_HOME` unset or `android/gradlew` absent) → exit 1 naming it. Never a silent skip.
- python leg: one child process per `local/webui/tests/test_*.py` module, run in parallel (`concurrent.futures`),
  `cwd=local`, `PYTHONPATH=local`; each child prints one JSON line per test `{id, seconds, outcome}`.
- shell leg: `bash src/build/test-build-item.sh`, one timed unit.
- android leg: `android/gradlew` (`gradlew.bat` when `os.name == 'nt'`, the only OS difference) `-p android
  testDebugUnitTest --console=plain`; per-test time and skips from
  `android/app/build/test-results/testDebugUnitTest/*.xml`.
- Constants: `TOTAL_BUDGET_S = 90`, `PER_TEST_BUDGET_S = 1.0`, `SLOWEST_SHOWN = 10`.
- Exit 1 when: any failure/error; wall time > 90 s; any test > 1 s (prints the slowest 10); grep finds `sleep` or
  `delay(` in `local/webui/tests/`, `src/build/test-build-item.sh`, `src/build/test-fixtures/`,
  `android/app/src/test/` (prints file:line). Always prints `skipped: N` per leg and in total.
- Linux gate (python + shell legs, the server's real platform):
  `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo
  relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell`.
- Android leg: `ANDROID_HOME=<sdk> python3 scripts/test.py --legs android` (this Mac:
  `/opt/homebrew/share/android-commandlinetools`; CI: runner image).

### 12.4 `tests/test_core_jobs.py` sleep rewrite (decision 10)
Replace every sleep with a state signal; keep each test's assertion:
- `wait_status`: wait on `jobs.log_condition(job_id)` with `cond.wait_for(predicate, timeout)` (final status is
  written before `Log.close()` notifies, `jobs.py:238-243`); `running` is observed through a `threading.Event` set by
  the test handler. `timeout` stays as a fail-fast limit.
- `test_serial_per_kind_parallel_across`: replace the 0.3 s hold with `threading.Event`s so the build handler waits
  until the ota handler has started (and the reverse is never true for two builds).
- Long-running child stand-ins `sleep 60` / `sleep 30` → `tail -f /dev/null`; pidfile wait → wait for the `go` line via
  `on_line` / log condition.
- Post-kill checks: wait for exit with `os.pidfd_open(pid)` + `select.select([fd], [], [], 5)`; these three tests read
  `/proc` and are decorated `@unittest.skipUnless(hasattr(os, 'pidfd_open'), 'needs Linux pidfd')` (on macOS they used
  to pass without checking anything; now the skip is visible in the count).
- `test_cancel_queued_never_runs`: instead of waiting 0.3 s, enqueue a third job and wait for its success (FIFO
  claim by id proves the cancelled job was passed).

### 12.5 Project `CLAUDE.md` (new, repo root)
States: test entry `python3 scripts/test.py`; full-suite budget 90 s (reason: Gradle daemon cold start for the
Android leg plus the Python suite measured at ~70 s serial on 85d44ea, `docs/logs/2026-10-25-per-app-contract.md`
parking lot); per-test cap 1 s; Linux gate command (§12.3); never push to an upstream remote, only `jhangyu/Relume`.

### 12.6 One-off checks (scratch, not committed; results into `docs/logs/`)
Portrait layout at 360 and 412 px per page; PWA install over HTTPS on the phone; migration 008 on a copy of the live
DB; every item of §13.

## 13. Verify on device (VoD)

1. Android 10/11: every install and update shows the system prompt; Android 12+: prompt-free update after the app
   became installer of record, for a target app above the OS floor; prompt for one below it.
2. Whether `UPDATE_PACKAGES_WITHOUT_USER_ACTION` is needed in addition to installer-of-record.
3. Handover over an ADB-installed package: one prompt, then `getInstallerPackageName` = companion.
4. Server ADB OTA still updates a package whose installer of record is the companion, without a phone prompt.
5. `getPackageArchiveInfo(..., GET_SIGNING_CERTIFICATES)` returns `signingInfo` for an archive on Android 10, and its
   SHA-256 equals the server's apksigner digest.
6. `STATUS_PENDING_USER_ACTION` while backgrounded: notification tap opens the system prompt; foreground start works.
7. `PackageInstaller.uninstall` shows a system confirmation and its result arrives.
8. A download worker stopped by the system resumes from `.part` via Range.
9. Cleartext to a `100.x` address and a `.ts.net` name works; a public `http://` URL is rejected in the app.
10. Keystore key readable from `CheckWorker` while the phone is locked.
11. Lower versionCode over an installed app is refused (justifies the uninstall dialog).
12. `REQUEST_INSTALL_PACKAGES` via "Install unknown apps"; `canRequestPackageInstalls()` reflects it after return.
13. `QUERY_ALL_PACKAGES` makes the patched apps and MicroG visible on API 30+.
14. PWA install from Chrome over the reverse-proxy HTTPS URL without a service worker.

## 14. Mobile web

### 14.1 PWA
- `static/manifest.webmanifest`: `name "Relume"`, `short_name "Relume"`, `start_url "/"`, `scope "/"`,
  `display "standalone"`, `background_color "#ffffff"`, `theme_color "#ffffff"`, icons `/static/icon-192.png` (192),
  `/static/icon-512.png` (512), `/static/icon-maskable-512.png` (512, `purpose "maskable"`).
- `server.ASSET` → `(css|js|svg|png|webmanifest)`; `CTYPES` adds `.png image/png`,
  `.webmanifest application/manifest+json`. Static assets stay public.
- Every page head: `<link rel="manifest" href="/static/manifest.webmanifest">`, `<meta name="theme-color"
  media="(prefers-color-scheme: light)" content="#ffffff">`, same for dark `#09090b`.
- No service worker.
- APK download menu (`app.js` `fillDownloads`) lists `arm64-v8a` first, then `universal`, then the rest.

### 14.2 Shared phone layer (`static/app.css`, `static/app.js`)
Under `@media (max-width:768px)`: touch targets ≥ 44 px (`.btn`, `.btn-sm`, icon buttons, `.switch`, `.menu button`,
`.chip`, `.handle`); `.item .acts{flex-wrap:wrap}`; `dialog{width:min(440px,92vw)}` plus `dialog form{overflow:auto}`
and no inline `min-width` on dialog forms; `100dvh` and `env(safe-area-inset-bottom)` on `.side`, `.pane`, `.toast`,
`.savebar`; `.toast` sits above `.savebar`. A shared top-bar overflow menu "⋯" component in `app.js` replaces
`.hide-m` page actions (not removal).

### 14.3 Per page
| Page | Change |
|---|---|
| index | overflow menu: Check upstream, status filter; group header overflow: Update all, Rebuild all; `dlg-add`/`dlg-src`/`dlg-group` inline min-width removed; per-item Update and group Update all call `POST /api/items/{id}/update {rebuild:true}` |
| item | `dlg-app`/`dlg-item` min-width removed; patch filter `width:100%` ≤ 768 |
| login | manifest/theme-color head only |
| projects | table → stacked rows ≤ 768 (name, version, status); "Check all now" into overflow menu |
| builds | `.steps` grid → vertical list ≤ 768; log toolbar wraps; history keeps #, Item, Result |
| schedules | `.seg` wraps; fixed 120/140 px inputs → `flex:1`; `.runs li span` min-width removed ≤ 768; timeline → list ≤ 768 |
| devices | app-device card: "Relume app", model, Android release, app version, last report; no host:port, Reconnect, Unpair, "Install after build"; Revoke link to Settings; "OTA all online" into overflow menu (ADB rows only); installed versions from `installed_reported` |
| settings | token row `flex-wrap:wrap`; new card "Phones with the Relume app": name, model, created, last seen, Revoke (`DELETE /api/devices/{id}/token`) |

`tests/test_ui_static.py` adds: every page has the manifest link; no inline `min-width` on `dialog form`.

## 15. Resolved conflicts

| # | Conflict | Resolution |
|---|---|---|
| 1 | App design: minSdk 34, no version branches | Decision 2: minSdk 29 with exactly two branches (§8.1) |
| 2 | App design uses `setPackageSource` (API 33) and `getInstallSourceInfo` (API 30) | Not used; `getInstallerPackageName` on all versions (§8.2). Would have been unapproved branches |
| 3 | App design: download worker as `dataSync` foreground service | No foreground service (§8.7); stopping is handled by Range resume. Avoids the API 31+ background start ban |
| 4 | App design: background job downloads only, install starts from a tap | Decision 8: background run does download, verify and commit; prompts via notification (§9) |
| 5 | App design §12.4: signer accept web-only | Decision 7: app can accept; route not session-only (§4.3, §10) |
| 6 | Server design: companion option A or B; layout plan default A | Decision 5: neither; no self-update, no `companion.py`, no `/api/companion` routes |
| 7 | Server design: `device.app_package` field to place the self step last | Dropped: no self-update (decision 5) |
| 8 | Server design: `GET /api/me/plan` ETag/304 | Dropped: the app never sends `If-None-Match`; `revision` stays |
| 9 | Server design: cookie `Secure` from `X-Forwarded-Proto` | Dropped: decision 6 says no server work for TLS |
| 10 | Server design: `SESSION_ONLY` lacks `DELETE /api/devices/{id}` | Added: deleting a row revokes another phone, same reason as the token route |
| 11 | Server design: project step `not downloaded` has no download for the app | `NOT_DOWNLOADED` error + server-enqueued pull (§5.1) |
| 12 | Migration NOT NULL `kind`/`created_at`/`at` would break existing INSERTs before the device slice lands | Column defaults (§3.1, §3.3) |
| 13 | App design: compileSdk/targetSdk "latest, 37"; this Mac has platforms 34–36 | compileSdk 37 is required by the AAR metadata of core 1.19.1 and lifecycle 2.11.0; platform 37 must be installed locally (plan, user item) |
| 14 | App design: notification channel `updates` + "N updates ready" | One channel `install` for the background run's prompts and results |
| 15 | Layout plan: `MainActivity.kt` in the build slice | Moved to the UI slice: it references `ui/` code, which does not exist in round 1 |
| 16 | Layout plan: `test_core_jobs.py` in the server artifacts slice | Own slice (decision 10) together with `jobs.py` |
| 17 | Layout plan: `test_ui_static.py` in the web shell slice | Moved to the web pages slice: the "every page has the manifest link" check can only pass after all pages change |
| 18 | Per-app spec cites `schema/006_app_schedule.sql`; the tree has `006_item_auto_update.sql` and `007_app_schedule.sql` | Code wins: next migration is 008 |
| 19 | Layout plan: `.ts.net`, `.local`, `.lan` names allowed over http (app design option a) | Decision 4: only `.ts.net` names, localhost and private/Tailscale IP ranges |
| 20 | Server design §7: `projects.html` "Check all now" not in the overflow list | Added (§14.3); found by grep of `hide-m` |
| 21 | Decision 11 counts 4 GitHub secrets | No GitHub secret: the release is published on this repo with `github.token` |

## 16. Parking lot (not in this work)
- Existing 1 s polling in `ota._pull_project` (`ota.py:222-232`).
- Unauthenticated `POST /api/auth/reset` can sign out all cookie sessions every 300 s.
- History list of app-side installs on the Devices page.
- `test_core_settings.py` health test reads `RELUME_VERSION` from env (fails inside a release-built image).
