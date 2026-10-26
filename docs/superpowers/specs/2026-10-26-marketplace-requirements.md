# Marketplace refactor — requirements

Status: approved by the user 2026-10-26. Input for the backend design, the UI design and the two implementation rounds.

## 1. Goal

Relume stops being configured as "patch source × app" items. It reads the community catalog that https://morphe-patches.software shows, lists it per app, and the user enables an app by picking one patch bundle for it. The service remembers the choice and keeps that app updated and rebuilt on one global schedule.

## 2. Catalog sources

- Primary: `https://morphe-patches.software/data/bundles.json` (unofficial, unversioned, no ToS; CORS `*`, ETag). Shape (2026-10-26 copy): `bundles[297]` {source github|gitlab, repo, branch?, name, author, patches[{name, description, default, compatiblePackagesKey}], targetApps[], appUpdates{}, stars, updatedAt, ...}, `compatibilities[]` (index target of `compatiblePackagesKey`; entries hold packageName, name, targets[{version}]; some entries are nested lists), `store{package: {name, category, iconUrl, description}}`, `featured[]`. 1207 apps, 1183 of them with version lists.
- Validation: strict at the boundary. A malformed or unreachable catalog fails the refresh loudly and keeps the last good copy in use; no silent defaults.
- Built-in source: `MorpheApp/morphe-patches` (stable). It is not in the community catalog. Its patch list is read from the repo's `patches-list.json` (format `{version, patches[{name, description, default, compatiblePackages[{packageName, targets[{version}]}]}]}`).
- Custom sources: the user adds a GitHub repo by URL. Engines: Morphe patches, and Xposed modules packaged with LSPatch or NPatch. Custom sources are not in any list; they appear in Marketplace merged with the catalog and labelled "custom".
- Every catalog record carries its origin (official catalog / built-in / custom). Adding a second catalog later (https://github.com/nvbangg/awesome-morphe `web/public/bundles.json`, hourly, MIT, 410 bundles) must need only a new parser plus de-duplication by repo, no schema or UI change. Not built now.
- Research: `docs/logs/2026-10-26-catalog-research-official.md`, `docs/logs/2026-10-26-catalog-research-awesome.md`.

## 3. Per-app model

- One enabled app = one package + exactly one chosen patch bundle → one output APK.
- Per-patch on/off overrides; defaults come from the bundle's `default` flag; overrides persist across versions.
- Version mode per app: auto (newest version the bundle supports; default), pinned, latest; plus the "allow nearest version" switch. Supported versions come from the catalog or the bundle's patch list.
- One enable switch per app replaces the separate Build (`item.enabled`) and Update (`item.auto_update`) switches.
- Enabling an app immediately runs the first fetch + build; it does not wait for the schedule.

## 4. APK download source

- Locate the app's APKMirror listing automatically from the package name: search `https://www.apkmirror.com/?post_type=app_release&searchtype=app&s=<package>`, then open candidate app pages and accept only an exact package-name match (search results are fuzzy).
- Not found or ambiguous → the UI asks the user to paste the APKMirror listing URL (APKPure optional); the app cannot build until it has one.
- Apps that already have URLs in `catalog-seed.json` / the `app` table keep them.
- APKMirror rate limits and 403/Cloudflare blocks happened before (`local-docker-build` memory, `docs/logs/2026-10-12-apkmirror-403-investigation.md`); the lookup must be rate-limited and its failure must be visible per app.

## 5. Schedule

- One global schedule interval. Each run: refresh the catalog → check the chosen bundle of every enabled app for a new release → check APKMirror for a new app version → rebuild what changed → notify.
- The OTA push schedule stays.
- Multi-target schedules (`schedules` kind `app` with item_ids/all and build/update/force/notify options) are removed.

## 6. UI (web and Android, same features on both)

- Web: Fluent 2. Android: Material Design 3, colourful (dynamic-colour style). Icon buttons are SVG.
- **Marketplace** tab: all catalog apps (official + built-in + custom), filter by store category, search, sort; expand an app to see its available bundles and, per bundle, the patches it offers for this app; pick one bundle, toggle patches, enable.
- **Apps** tab: enabled apps only. Columns: app name, app version, patch bundle used, status. Actions: check for update, rebuild, download. Here the user can also change the bundle, toggle patches and set the version mode.
- Kept pages, restyled only: build history, devices and OTA, Follow projects, settings. Schedules page shrinks to the global interval plus OTA schedules.

## 7. Removed (no backward compatibility; project is unreleased)

- Custom groups (`grp` table, `item.group_id`, group UI).
- The "source × app" item model and per-source configuration UI, the separate Build/Update switches, multi-target schedules.
- Orphan `upstream` rows (22 after the 2026-10-26 source cleanup) and the leftover `apk-cache` package dirs of deleted apps.
- Their tests and docs go with them.

## 8. Data migration

- Current DB (after the 2026-10-26 cleanup, backup `local/data/webui.db.bak-2026-10-26`): one source `morphe` with YouTube (enabled), Reddit (enabled), YT Music (disabled).
- Migrate: YouTube and Reddit → enabled apps on the built-in source with their patch overrides and version mode; YT Music → not enabled.

## 9. Team process

1. Exploration: 2 sonnet members (backend; web + Android front end). Reports in `docs/logs/`.
2. Design, in parallel:
   - 3 opus backend designers by subsystem: (1) catalog layer — fetch/validate, multi-catalog room, built-in + custom sources incl. Xposed, bundle patch lists; (2) app and build flow — enabled-app model, single switch, global schedule, removal of old tables and groups, migration; (3) APKMirror auto-locate and the API shapes for web and Android. Lead merges into one spec.
   - 3 opus UI designers, each a complete candidate (web Fluent 2 + Android MD3 colourful, HTML mockups). The user picks one or a mix.
3. Implementation: 2 rounds, 4–5 implementers each. Reviewer only in the last round. No test runner.
