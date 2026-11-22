# Contract: detach the full app inventory from the companion sync

Frozen 2026-11-23. Only the user can change it.

## End state
The companion's startup and every regular sync read and report only the Relume-managed (tracked) packages; the full installed-app inventory (all user apps with sizes and icons, plus icon uploads) runs as a separate background task that never blocks the UI or the regular sync.

## Root cause (observed)
`Inventory.sync()` (android/app/src/main/kotlin/io/github/jhangyu/relume/inventory/Inventory.kt:113-131) calls `AppInventory.read` (all apps, sizes, icons) and `storeInventory` (icon uploads) inside `syncLock`. Callers: `ui/AppModel.kt:202` (startup/refresh), `install/InstallWorker.kt:180`, `sync/CheckWorker.kt:29`.

## In scope
1. Companion: the regular sync no longer reads the full inventory nor uploads icons.
2. Companion: a separate background task (WorkManager or app-scope coroutine; follow existing patterns) reads the full inventory, sends it to the server, uploads icons, saves the diff snapshot. It does not hold the regular sync lock during the slow read. It runs after the regular sync / on the existing 15-minute schedule; App Manage actions the server returns from it are still executed (ActionExecutor).
3. Server (src/): only if the server cannot accept a sync without inventory or an inventory-only report — minimal change, with its tests.

## Out of scope
New UI, App Manage page changes, parking-lot items of the 2026-11-20 signoff, releases.

## Acceptance
1. `Inventory.sync` (or its replacement on the startup path) contains no call to `AppInventory.read` and no icon upload (grep).
2. The full inventory task exists, is scheduled/triggered, and does not take `syncLock` during `AppInventory.read` (code read).
3. App Manage actions returned by the server in any response are still passed to ActionExecutor (code read + caller list).
4. The server still receives the full inventory: the inventory report path keeps the diff/full-resend/icon-upload/snapshot behavior (code read; server tests green if server changed).
5. `python3 scripts/test.py` green (python, shell, android legs) within the 90 s budget.
6. Companion `assembleDebug` builds.

## Budget
3 rounds.

## Parking lot
(empty)

## Sign-off (2026-11-23)
Commit 08344c7 (unpushed). All 6 acceptance items pass. No server change: local/webui/ota.py sync() accepts a report without inventory; App Manage actions are picked up only with an inventory.
- python3 scripts/test.py at 08344c7: RC=0, python 592 / shell 30 / android 77, 0 failed, 8 skipped, wall 10.9 s.
- assembleDebug at 08344c7: RC=0.
Known behavior changes: App Manage actions wait for the background inventory run; a periodic check's background install plan now starts before App Manage build actions; tracked packages are posted twice per cycle (hashes cached). Not tested on a device; Linux docker gate not run (no server change).
