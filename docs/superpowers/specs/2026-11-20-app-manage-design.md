# App Manage — design

## Goal
A new, separate "App Manage" page that shows which apps every Android device has, against one primary device, and installs, upgrades and removes apps on the other devices from one place. Main use: removing unwanted apps and keeping secondary devices in sync with the primary.

## User decisions
| Topic | Decision |
|---|---|
| App scope | All user-installed (non-system) apps on each device, not only Relume-tracked apps. |
| Page | New page with its own sidebar entry. Not part of the devices page. |
| Install source | Relume-tracked app → existing server build. Other app → Google Play direct download (automatic); fallback per action: open the Play Store page on the phone (user taps install). |
| Play direct download | The server resolves Play delivery URLs (base + splits) at pickup time with the existing apkeep fork (`-o probe=session`, stdin `<versionCode>`) and the Play token; the phone downloads straight from Google; the server stores no APK. Probe: `docs/logs/2026-11-20-play-url-probe.md` (portable across clients; cross-IP checked at the first live run on mobile data). If the live run shows the URLs are IP-bound, the user's chosen fallback is an in-app Play client on the companion — a separate follow-up, not this batch. |
| Remove | Remove button in every installed cell; batch install and batch remove on the primary cell (dialog with device checkboxes). Companion phones show the system confirm dialog. |
| Outdated | A cell shows Upgrade when its version is lower than the highest version across all devices and, for Relume-tracked apps, the latest Relume build. The primary cell can show Upgrade too. |
| Companion latency | Keep the periodic sync and add a 15-minute option to the existing OFF/1h/6h/24h/daily choices (`Prefs.kt`, `CheckScheduler.kt`); the default is unchanged. Cells show "waiting for phone sync". Opening the companion syncs at once. |
| Architecture | One unified device action queue for both device kinds. |
| Layout | `docs/mockups/app-manage.html` (dense-grid): spreadsheet matrix, sticky header, app column and primary column, resizable column widths remembered per browser, status popups never clipped. |

## Decisions made without asking (reported to the user)
- Size = app + data + cache; the tooltip shows the split.
- Row order: apps not on the primary first, by the largest size among the secondaries, descending; then apps on the primary, by the primary's size, descending; package name breaks ties and covers unknown sizes.
- Secondary columns are sorted by device name. Device header = generic phone icon + name.
- The primary device is picked from a selector at the top of the page and stored server-side in settings.
- An offline device shows its last reported data and the report time; actions on it queue.
- Removing a Relume-tracked app also deletes its assignment for that device; installing one also writes the assignment, so existing auto-update keeps working.
- Column widths are a per-browser convenience in `localStorage`.

## 1. Data model
- **Device app inventory** (new table): device, package, label, version code, version name, app/data/cache bytes (nullable = unknown, with the reason), system flag, reported at. Separate from `device_packages`, which stays the Relume-tracked OTA record.
- **App icons**: the companion uploads a PNG per package + version code on demand; the server stores it in the data dir and serves it. Existing catalog icons stay as they are.
- **Device actions** (new table): device, package, action (`install`, `upgrade`, `remove`), source (`build`, `play_direct`, `play_page`), state (`queued`, `delivered`, `running`, `done`, `failed`, `cancelled`), failure reason, timestamps. A new action for the same device + package replaces an unfinished one.
- **Primary device**: one device id in the existing settings store.

## 2. Android companion
- `QUERY_ALL_PACKAGES` is already declared. Reads sizes with `StorageStatsManager`, which needs the usage-access special permission; the app detects it and shows a button to the settings screen. Without it the sizes are reported as unknown, never as 0.
- The inventory is reported inside the existing sync: only changed entries, user apps only, each with label, version, sizes and an icon hash. The server answers with the icon hashes it lacks and the phone uploads those PNGs.
- Executes actions from the sync:
  - `build`: the existing download + PackageInstaller flow.
  - `play_direct`: downloads the base and split APKs from the URLs the server gives then installs them in one PackageInstaller session.
  - `play_page`: posts a notification; tapping it opens the Play Store page. Background apps cannot start activities.
  - `remove`: the system uninstall flow, which shows a confirm dialog.

  Every result is reported back: done, cancelled by the user, or failed with the reason.

## 3. Server
- New route module `appmanage` (registered in `server.py` `CHANGES`), with a new SSE topic `appmanage`:
  - matrix read: devices, apps, cells, row order and highest version, all computed on the server
  - set primary device
  - create actions: one cell, or batch for one app × many devices
  - cancel a queued action
- Companion API, extended on the existing `/api/me/*` sync:
  - inventory and icon upload
  - action pickup: `play_direct` URLs are resolved at pickup time and never stored as files or logged in full. A new mapping picks the apkeep device profile from the device row (ABI, sdk, density); existing profiles: three single-ABI ones and `nothing_p1` (`build.py:19-20`)
  - action result report
- ADB devices: the inventory comes from `pm`/`dumpsys`. Actions run as server jobs: `adb install` (the `play_direct` download is streamed into adb without caching), `adb uninstall`, and `am start` for the Play page.
- Errors: each failure records expected, actual and the affected device + package, and is shown on the cell. A failed Play URL lookup (token, rate limit) fails the action with the reason; there is no silent switch to another source.

## 4. Web page
Follows the dense-grid mockup. It reuses the shared `app.js`/`app.css` components (`.btn`, `.badge`, `.tile`, `.seg`, `.select`, dialog, toast, `iconTile`, the Fluent 20 regular sprite) and adds only the matrix CSS. It is light-theme only, like the rest of `app.css`.

## 5. Testing (project policy: `python3 scripts/test.py`, 90 s total, 1 s per test)
Committed tests cover core logic only:
- row ordering and the outdated computation
- the action queue's replace/cancel rules and pickup with state transitions
- inventory diff ingest
- authorization: a companion token only reaches its own device's actions

No UI, browser or screenshot tests. The Play URL probe and red-to-green proofs live in `scratch/` and their logs in `docs/logs/`.

## Preflight
Done: `docs/logs/2026-11-20-play-url-probe.md` and `docs/logs/2026-11-20-app-manage-preflight.md`.
