# Marketplace spec + plan — author report (2026-10-26)

Files: `docs/superpowers/specs/2026-10-26-marketplace-design.md`, `docs/superpowers/plans/2026-10-26-marketplace.md`. No code edits, no git writes. The live DB was read only with `sqlite3 -readonly`.

## Round split (plan §1, §7, §8)
No file is owned by two tasks in the same round.

Round 1:
- 1.1 Migration and shared fixtures (opus): `schema/010_marketplace.sql`, `db.py`, `tests/catalog_rows.py` + `device_helpers.py`, `ota.py`/`notify.py` label SQL, fixture rows in the device and project tests, live migration check on a `.backup` copy.
- 1.2 Enabled apps and build flow (opus): new `apps.py`, `build.py`, `scheduler.py`, `jobs.py` defaults, `__main__.py`, build shell scripts, `test_apps.py`; deletes `test_build_zip.py`.
- 1.3 Catalog layer (opus): rewrites `catalog.py`, trims `upstream.py`, adds the `projects.py` pull route, updates the `settings.py` http_get caller, adds `test_catalog_feed.py` + fixtures; deletes the seed file and `test_catalog_seed`/`store`/`xposed`.
- 1.4 APKMirror locator and HTTP API (opus): new `apklocate.py`, `market.py` (21 routes), `fetch-page.sh`, `server.py` (HttpError code field, MODULES), `test_apklocate.py`, `test_market_api.py`.
- 1.5 Android foundation (sonnet): MD3 fixed palette, components, Material Symbols drawables, API client and models, three tabs with Settings behind the avatar, ports of the kept screens, thin first versions of the new screens, `MarketParseTest`; deletes `DetailScreen`.
- Ordered first commits: 1.1 schema + fixtures; 1.2 boot without `seed_import`; 1.3 http_get 4-tuple + small lookup functions; 1.4 HttpError code field.

Round 2 (depends only on committed round-1 output):
- 2.1 Web foundation + Marketplace page (sonnet): `app.css`, `app.js`, `index.*`, `test_ui_static.py`.
- 2.2 Web Apps page (sonnet): `apps.*` new; deletes `item.*`.
- 2.3 Web Schedules, kept pages, README, FAQ (sonnet).
- 2.4 Android Marketplace (sonnet).
- 2.5 Android Apps, Install, App settings, Schedules, Settings (opus).
- Reviewer (opus): runs in round 2 only and runs the full gates (plan §6). The round-1 gate is the lead's gate step (plan §5).

## Resolved conflicts: 25 (spec §12). The important ones
- A malformed bundle is skipped and recorded in `catalog_feed.skipped`. The whole file is rejected only when it is not JSON or its root shape is wrong.
- An app can be enabled without a download URL. This overrides the mockups.
- The update run calls the APKMirror locator whenever a lookup is due. Manual locate resets the state to pending first.
- Interface names are unified: `market_apps`, `feed_status`, `default_enabled`, `not_selectable_reason`, `list_enabled`.
- `icon_url` is always the icon route. A failed fetch is a recorded 404, and the client shows a letter tile.
- 20 orphan upstream rows are deleted, not 22: the LSPatch and NPatch CLI rows are kept.
- The built-in bundle is created by the migration SQL. A fresh DB needs no boot step for it.
- Android has no dynamic colour, uses official Material Symbols drawables, and has Install instead of Download. Custom sources work on Android.
- The schedule "Custom" form has no cron field. It uses the existing day/week/hours forms.
- New route `POST /api/projects/{id}/pull` (replaces the deleted upstream pull route).
- Install first assigns the app to the phone, then runs the existing plan, which also installs the Follow-project dependencies.

## Open questions for the user
None. Two resolutions to relay to the user:
- The schedule form has no cron field. This deviates from the web mockup.
- Install auto-assigns the app to the phone, so that phone also receives OTA updates for the app.

## Uncertainties
- Android targeted Gradle runs fit in 30 s only with a warm daemon. The plan tells implementers to report BLOCKED instead of running in the background.
- The Linux-gate Docker build may exceed the foreground limit. In that case the user runs it.
- The plan does not inline full source for each step, unlike the writing-plans template. It does fix signatures, the SQL skeleton, test names and greps.
- The migration deletes the Threads fork upstream rows (27, 28) as orphans.
- The material3 carousel and SearchBar APIs are not verified. Implementers must check them with context7.
- The Chrome markers used by the render check have not been tested.
