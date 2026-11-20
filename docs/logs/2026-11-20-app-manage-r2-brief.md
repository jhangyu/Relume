# App Manage — round 2 brief (base f3dff8f)

Round 1 is signed off and committed: 679dff0 (matrix, ADB reader, Play resolve), 0fcd9c9 (web page), f590e97 (companion inventory report), f3dff8f (migration 024 + store). The plan §2 round 2 tasks apply, with the amendments below. The contract (`docs/logs/2026-11-20-app-manage-contract.md`) was amended by the user: in-scope 11 (per-device locale), acceptance 8 (8 named Linux/root-only skips allowed on macOS), 9 (Linux gate skip 0), 14 (locale tests).

## Facts from round 1 that round 2 must use
- `devices.assign_in(c, …)` and `devices.unassign_in(c, …)` take the caller's connection; the store writes and deletes assignments in `create_actions` through them. `assign_one` and `unassign_one` wrap them.
- `ota.install_ref` plans only assigned, enabled items, so a build-source action needs its assignment, which `create_actions` writes.
- A failed install receipt with no code is recorded as `INSTALLER_ERROR`.
- The web page always sends `source` for install and upgrade, offers a per-action source choice, and offers "Open Play Store page instead" on a failed `play_direct` cell.
- Companion: `Inventory.sync` sends the inventory diff on every call. The callers are CheckWorker, AppModel, and `InstallWorker.finishRun`. Syncs are serialized. The phone sends a full report once after a rejected diff.
- `store ↔ ota` imports are function-level, so the cycle is fine.

## Amendments
- **A1 (R2-D):** the actions returned by every `Inventory.sync` must run, `InstallWorker.finishRun` included. R2-D also owns `android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallWorker.kt`, only to run `ActionExecutor` after its sync. No sync response may drop actions.
- **A2 (new R2-F, locale):** frozen transport, so R2-B, R2-D and R2-F can run in parallel:
  - **Companion sync body:** a new optional top-level field `"locale"`, a BCP-47 tag of the phone's first system locale, for example `"zh-TW"`. It must match `^[a-z]{2,3}(-[A-Z]{2}|-[A-Za-z]{4}(-[A-Z]{2})?)?$`. R2-F adds the field to the Android sync DTO and fills it (`Models.kt` and `Inventory.kt`, only for this field).
  - **Server:** R2-B parses the optional `locale` in `ota._parse_sync`; a bad shape is 400 naming the field. It stores the value in `devices.locale` (a non-legacy row only) with the sync update.
  - **Migration:** R2-F owns `local/webui/schema/025_device_locale.sql`, which runs `ALTER TABLE devices ADD COLUMN locale TEXT`. R2-F writes this file first and messages `r2b-sync` when it applies, because R2-B's tests depend on the column.
  - **ADB devices:** R2-F adds `devices.adb_locale(dev)`, which reads `getprop persist.sys.locale` and falls back to `ro.product.locale`. An empty value raises.
  - **Play lookup:** `appmanage_play.resolve(package, dev, log)` keeps its signature. It uses `dev['locale']` for app rows and `devices.adb_locale(dev)` for ADB rows, and passes the locale to apkeep. A missing locale fails with `PLAY_LOOKUP_FAILED` and a reason that names the device. There is no silent English default.
  - **apkeep:** R2-F first checks, in the pinned EFForg/apkeep source and its gpapi dependency, whether a locale can be set per request. If a change is needed, R2-F extends `local/apkeep/apkeep.patch`. The Docker image rebuild that proves it is run by the user: R2-F reports the exact command, then STOP. If no locale can be set at all, R2-F reports BLOCKED with evidence.

## Round 2 tasks and owner files
- **R2-A routes and wiring (opus):** `appmanage.py`, `server.py`, `events.py`, `auth.py`, `tests/test_appmanage_api.py`.
- **R2-B sync and adopt (opus):** `ota.py`, `tests/test_appmanage_sync.py`. It also does the A2 locale parse and store.
- **R2-C ADB job (sonnet):** `appmanage_job.py`, `jobs.py` (OPTION_DEFAULTS only), `tests/test_appmanage_job.py`.
- **R2-D Android executor (opus):** the plan list plus `install/InstallWorker.kt` (A1).
- **R2-F locale (opus):** `local/apkeep/apkeep.patch`, `local/Dockerfile` (only if the apkeep build needs it), `schema/025_device_locale.sql`, `appmanage_play.py`, `devices.py` (`adb_locale` only), `tests/test_appmanage_play.py`, Android `api/Models.kt` and `inventory/Inventory.kt` (the locale field only).
- **R2-E integration (sonnet):** runs after the other tasks are signed off. It also adds the live check "a zh-TW phone's `play_direct` install gets a `config.zh*` split".

Unprefixed paths are under `local/webui/`.
