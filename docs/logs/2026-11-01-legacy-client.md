# Legacy client (companion <= v1.2.0, no device_uid) — 5111bc1

Old app requests (v1.2.0 Models.kt/RelumeApi.kt): sign-in `{username,password,device:{name,model,sdk,android_release,abi_list}}`; sync `{device:{model,sdk,android_release,abi_list,app_version_code},packages}`; receipt kind/ref_id/result + artifact_id|tag (+error_code), nulls omitted. Health check: needs server >= 0.0.4.
Only rejections: auth.py:192-193 (sign-in) and ota.py:350 (sync), both device_uid. Everything else changed since v1.2.0 is compatible.

Self-upgrade path: no server special case. The old app upgrades itself only if the companion APK is a followed project (package io.github.jhangyu.relume) assigned to the phone: sync plan shows an install step, the app downloads from the project APK route, installs, posts a project receipt with the tag. If that project is not set up/assigned, no server change gives the old app an upgrade path. Live server config not checked.

Fix: ota.parse_device_uid(device) returns None when the key is absent; a present value is validated as before (null/''/'a/b'/7 -> 400). Sign-in with no uid: new row, uid NULL, adb takeover offered. Sync with no uid: skips _adopt_device_uid. No version sniffing: key absence is the signal.
Known limit: a legacy phone signing in again gets a new row each time (NULL uid cannot match).

Tests: new local/webui/tests/test_legacy_client.py (2 tests, 0.85 s). Red before fix (auth.py/ota.py at HEAD): `AssertionError: 400 != 200 : b'{"error": "device.device_uid must be 1-64 letters or digits, got None"}'`. Green after. `python3 scripts/test.py --legs python` RC=0, `python: 428 tests, 0 failed, skipped: 8`.
Changed existing test: test_core_auth.py (missing uid no longer 400; added device_uid None case). test_sync_uid.py unchanged, passes.

## Legacy install-only mode (replaces 5111bc1 behaviour) — de5a4b6

Design: schema/013_legacy_device.sql adds `devices.legacy` (kept kind='app' so the kind CHECKs and all `kind='adb'` queries stay valid, no table rebuild).
- auth.py register_device: no uid -> row legacy=1, `adb_devices` empty (a legacy phone gets a new row per sign-in, accepted).
- ota.py: `SELF_PACKAGE`/`_legacy_scope` (plan scope: ids asked for = any enabled item / any project; none asked = items+projects with package io.github.jhangyu.relume), `_plan_for` branch, `_me_json` tracked list = companion only, `list_devices` hides legacy, `me_plan` skips the assigned check, `post_install` returns 200 before `_receipt_target`, `sync` (`_leave_legacy`: convert in place, or move token to the row owning the uid and delete the legacy row).
- auth.legacy_refused + server.py dispatch: legacy token, non-GET on /api/devices* or POST /api/me/adopt -> 403 (`LEGACY_DENIED_MSG`). devices._dev_or_404 and projects._device_ids treat legacy as not found; scheduler/after-build/notify already `kind='adb'`-only.
Companion identification: package match on item/project rows, not a fixed id. No such row -> plan empty, tracked list empty (not verified on the live server).
Uncertainty: legacy sync stores the companion's own version in device_packages (needed so the plan says up_to_date after upgrade) but writes no device facts / last_seen. Old app's reaction to the 403 is unverified (it shows ApiError text per RelumeApi.errorOf; the install flow never calls these writes).
Tests: tests/test_legacy_client.py, 9 tests. Red before code: `sqlite3.OperationalError: no such column: legacy` plus `AssertionError: 200 != 403` (assignments PUT) and `400 != 200 : item not assigned to this device`. Green after; `scripts/test.py --legs python`: 435 tests, 0 failed, skipped 8; exit 1 only from the 1.0 s cap on other files' tests under load (test_core_scheduler 1.6 s, test_core_sse 1.3 s, test_catalog_inspect 1.2 s, test_core_settings 1.2 s).
