# Marketplace backend part 2: enabled-app model and build flow

Input: `docs/superpowers/specs/2026-10-26-marketplace-requirements.md` (binding), `docs/logs/2026-10-26-explore-backend.md` (code map at `243e56c`). Code facts below were re-read at HEAD `991710b`. `w/` = `local/webui/`.
Scope: the enabled-app row, every item-keyed reference, the build recipe and version resolution, the global update run, removals, the one schema migration, tests. Catalog storage (bundles, sources) is part 1 (be-catalog); APKMirror lookup and HTTP shapes are part 3 (be-api).

## 1. Decisions in one list

1. The enabled app stays the `item` row, keyed by its integer `item.id`. The table is rebuilt (columns change), ids are kept. Every reference that is keyed on `item.id` today (artifacts, build jobs, device assignments, OTA receipts, job targets, Android plan steps) keeps working without rewrite. Reason: those references span seven tables, disk paths and the shipped Android client (`android/.../api/Models.kt:19,36,114,127` uses kind `"item"` and an integer `ref_id`); renaming buys nothing functional. The HTTP route names are part 3's choice.
2. One row per package (`UNIQUE(package)`), one bundle per row (`bundle_id` FK to part 1's `bundle` table). `source_id`, `key`, `group_id`, `position`, `auto_update` go. `enabled` becomes the single switch.
3. Disabling keeps the row (bundle choice, overrides, version mode survive); enabling again reuses it. There is no delete action for an app (YAGNI); a disabled row is invisible in the Apps tab and shows as "remembered choice" in Marketplace.
4. Version resolution moves from shell to Python. `job.json` carries the final version (or "newest on APKMirror"); `detect_version` and its three call sites go.
5. "Needs a rebuild" becomes one rule: the fingerprint of the build inputs differs from the fingerprint stored with the last successful build. This covers bundle release, CLI release, new APKMirror version (mode latest), pin change, patch toggles, option values, bundle switch and variant changes with one comparison.
6. The global schedule is one `schedules` row of kind `update`. Its run, the "check for update" button and "enable" are the same job (kind `upstream`, target `{all:true}` or `{item_id}`), executed in the existing single `upstream` worker, so all GitHub and APKMirror traffic stays serial and pulls never race with Follow-project pulls.
7. The `appcheck` job kind goes (folded into the update run). No new job kind is added.
8. The seed file goes; its 41 `app` rows (40 APKMirror list URLs, 40 example URLs, 1 APKPure URL) become literal `INSERT OR IGNORE` rows in migration `010`, so fresh and migrated databases both have them.

## 2. Data model

### 2.1 `item` (rebuilt in 010)

```sql
CREATE TABLE item (
  id INTEGER PRIMARY KEY,
  package TEXT NOT NULL UNIQUE REFERENCES app(package),
  bundle_id INTEGER NOT NULL REFERENCES bundle(id),
  display_name TEXT,                                   -- NULL = app name
  enabled INTEGER NOT NULL DEFAULT 1,                  -- the one switch (requirements §3)
  version_mode TEXT NOT NULL DEFAULT 'auto' CHECK (version_mode IN ('auto','pinned','latest')),
  version_pin TEXT CHECK ((version_mode = 'pinned') = (version_pin IS NOT NULL)),
  allow_nearest INTEGER NOT NULL DEFAULT 0,
  variants TEXT NOT NULL,                              -- JSON, unchanged shape (catalog.py:196-236)
  patch_overrides TEXT NOT NULL DEFAULT '{}',          -- JSON {patch: {enabled?, options?}}, unchanged (catalog.py:635-670)
  engine_options TEXT);                                -- JSON {sigbypass, injectdex} for module engines, else NULL
```

- `bundle_id` has no `ON DELETE`: part 1 must refuse to delete a bundle (custom source) that an item uses, like `projects.delete_project` refuses a project with dependants (`projects.py:450-451`).
- Patch defaults come from the downloaded bundle (`bundle_info.patches[].default_enabled`); overrides persist across versions; stale overrides (patch no longer in the bundle) stay stored and are dropped from the build input, as today (`catalog.stale_overrides` `catalog.py:689`, `job_input` `:723-727`).
- Changing the bundle keeps `patch_overrides`: entries for patches the new bundle does not have become stale and are ignored; same-name patches keep the user's choice. `engine_options` is reset to the engine default when the engine kind changes (patches ↔ module), validated by the existing `validate_engine_options` (`catalog.py:260`).
- Module engines (lspatch/npatch) keep their variant rules (`check_xposed_variants` `catalog.py:275`) and have no patch list (`reject_module_item` `catalog.py:314`).

### 2.2 Unchanged tables that hang off `item.id`

`item_dependency(item_id, project_id)` (Follow-project dependencies, requirements §3), `build_jobs`, `artifacts`, `device_assignments` keep their columns. `build_jobs` gains one column:

```sql
ALTER TABLE build_jobs ADD COLUMN inputs_sha TEXT;   -- fingerprint of the build inputs (§4.3); NULL for builds before 010
```

### 2.3 `app` (kept)

Kept as the per-package record: download URLs (`apkmirror_list_url`, `apkmirror_example_url`, `apkmirror_example_url2`, `apkpure_url`), `apkmirror_versions` (newest first), icon. Added by 010:

Part 3's locator-state columns are added in the same migration: `apkm_state TEXT CHECK (apkm_state IN ('pending','found','manual','not_found','ambiguous','blocked','failed'))`, `apkm_candidates`, `apkm_error`, `apkm_checked_at`, `apkm_retry_at`; seeded/migrated rows with a list URL get `apkm_state='manual'`. Version-check failures are written to the same `apkm_error` (one per-app error column). `app.display_name`, `app.icon_file`, `app.icon_fetched_at` are dropped: names and icons move to part 1's `store_app`. Rows are created by enable (`INSERT OR IGNORE`) and by the seed rows in 010.

### 2.4 Global schedule (in `schedules`)

`schedules.kind CHECK IN ('update','ota')`. Exactly one `update` row (partial unique index `CREATE UNIQUE INDEX schedules_one_update ON schedules(kind) WHERE kind = 'update'`), target `{}`, options `{notify: bool}`, spec = any existing spec form (`scheduler._check_spec` `scheduler.py:61`: daily at, weekly days+at, every N hours). It cannot be created or deleted through the API (POST/DELETE with kind `update` → 400/409); PUT edits spec, enabled, notify. OTA rows unchanged.

## 3. Item-keyed references: new key and rewrite per site

The key is `item.id` before and after; ids are preserved by 010. What changes is only joins through removed tables.

| Site | Today | After |
|---|---|---|
| `artifacts.item_id`, disk `artifacts/<item_id>/<job_id>/` | FK item | unchanged |
| artifact file name `build.artifact_name` (`build.py:89`) | `<source_id>-<key>-<variant>-<ver>-<tag>.apk` | `<package>-<variant>-<ver>-<tag>.apk` (old files keep their stored `path`) |
| `build_jobs.item_id` | FK item | unchanged, + `inputs_sha` |
| `jobs.target` build `{item_id}` (`build.py:116`, `STATUS_SQL :316-320`, `jobs.list_route :427`) | json item_id | unchanged |
| `jobs.target` appcheck `{package, pressed}` | kind appcheck | kind removed, rows deleted in 010 |
| `jobs.target` upstream `{upstream_id, pressed}` | pull | `{upstream_id}` (Follow projects, OTA project pull `ota.py:279,543`), `{all:true}` (scheduled run), `{item_id}` (one app); `pressed` goes |
| `device_assignments.item_id` (`devices.py:425-446`) | FK item | unchanged |
| `ota_installs.ref_id` kind `item` | no FK | unchanged; 010 deletes rows whose item is gone |
| `ota_jobs.item_ids` | JSON | unchanged (history) |
| Android `/api/me/plan` step `ref_id`, receipts (`ota.py:125,414-420`) | item id | unchanged |
| `ota.assigned_items` (`ota.py:21-33`) | joins `source` for label, `grp` for order | label `COALESCE(item.display_name, store_app.name, item.package)`; order `ORDER BY item.id` |
| `ota.on_build_succeeded` (`ota.py:694-699`) | item_id | unchanged |
| `ota.py:56` package list | `item.package` | unchanged |
| `notify._label` (`notify.py:69-72`) | joins `source.name` | `COALESCE(item.display_name, store_app.name, item.package) || ' · ' || bundle.name` (LEFT JOIN store_app, JOIN bundle) |
| `notify.py:101` new-version text | `item_ids` count | unchanged; payload from the new `affected_items` |
| `projects.item_projects`, `check_assignment` (`projects.py:95,147`) | item | unchanged |
| `upstream.affected_items` (`upstream.py:140`) | joins `source` | joins `bundle` (`b.upstream_id = ?`; for a CLI row from `catalog.engine_cli`, every item whose bundle has that engine) + dependency projects, enabled items only |
| `upstream._packages` (`upstream.py:387`) | joins `source` | `SELECT DISTINCT i.package FROM item i JOIN bundle b ON b.id = i.bundle_id WHERE b.upstream_id = ?` |
| `upstream.pull` CLI pre-pull (`upstream.py:436-440`), `inspect_row` CLI (`:288`) | `source.cli_upstream_id` | `catalog.engine_cli('morphe')` (part 1) |
| events `build.*` payload `item_id` | | unchanged |

## 4. Build flow

### 4.1 `job.json` (contract of `src/build/build-item.sh`)

Kept fields: `item_id, package, engine, engine_options, upstream_tag, cli_tag, patches_files, cli_jar, options_file_json, apkmirror{list_url, example_url, example_url2, versions_due}, apkpure_url, keystore, recipe{variants}, job_id, work_dir`.
Changed: `version = {value: "<version>" | null, near: bool}`. `value` null means "newest on APKMirror" (today's `latest` with `lock_version=1`). `mode` goes.
Removed: `source_id`, `key` (only used for the artifact name).

Shell changes (`build-item.sh:54-75`): replace the `case "$mode"` block with
`version=$(j .version.value); [[ -z "$version" ]] && lock_version=1`.
Delete `detect_version` (`utils.sh:45-57`) and its calls (`build-item.sh:66`, `utils.sh:168`, `utils.sh:425`; with `version` set or `lock_version=1` they are already no-ops) and the symlink comment `build-item.sh:50` (the symlinks themselves stay: `patch_variant` uses them). `version_unresolved` leaves `FAIL_CODES` (`build.py:16`); no path produces it any more.

`catalog.job_input` moves to the app module (§4.5) and reads `item JOIN bundle JOIN app JOIN upstream u (bundle.upstream_id)`, the CLI row from `catalog.engine_cli(bundle.engine)` and the keystore from the constant `apps.KEYSTORE = {'morphe': 'morphe.keystore', 'lspatch': 'lspatch-signing.p12', 'npatch': 'fiorenmas.ks'}` (`source.keystore` goes; the shell's `keystore_creds`, `build-item.sh:21-27`, already accepts only these). Patch list = `catalog.patch_list(bundle_id, package)` (replaces `_bundle`, `catalog.py:585`). It raises `UpstreamMissing` as today (`catalog.py:715-722`) and additionally when the engine is morphe and the patch list is None ("patch list missing for <package> at <tag>").

### 4.2 Version resolution (Python, at job-input time)

`want_version(item)`:
- `pinned` → `version_pin`.
- `auto` → newest (by `version_key`, `catalog.py:23`) of the union of `compatible_versions` of the bundle's patches for this package in `bundle_info` at the downloaded tag. When that union is empty (the bundle supports any version, or a module engine) → same as `latest`. Today the empty case fails `version_unresolved` (`build-item.sh:67`) and module engines reject `auto` (`catalog.py:319-321`); both rules go, `check_version_mode` is deleted.
- `latest` → `app.apkmirror_versions[0]` for the fingerprint; `job.json` gets `value: null` so the runner downloads the newest it sees at build time (unchanged behaviour).

`allow_nearest` is passed through unchanged (`near_version=1` in `get_apk`).

The catalog's `compatibilities[].targets` (part 1) are for Marketplace display only; the build uses the downloaded bundle, the thing that is actually patched. One source of truth per purpose.

### 4.3 Rebuild rule (`needs_build`, replaces `build.py:345-360`)

`inputs(item)` = canonical JSON (`jobs.canon`) of `{bundle_id, patches_tag, cli_tag, version: want_version, overrides (non-stale), engine_options, variants}`; `inputs_sha = sha256(inputs)`, written into `build_jobs` at job start next to `patches_version/cli_version` (`build.py:132-134`).

`needs_build(item)` is true when: no successful build, or the last finished build failed, or no artifact row is left (cleaned up), or the last success's `inputs_sha` ≠ current `inputs_sha`.

Per mode this gives:

| Change | auto | pinned | latest |
|---|---|---|---|
| new bundle release downloaded | rebuild (tag and maybe version changed) | rebuild | rebuild |
| new CLI release downloaded | rebuild | rebuild | rebuild |
| new version on APKMirror | no (the bundle decides the version) | no | rebuild |
| pin changed / mode changed | rebuild | rebuild | rebuild |
| patch toggle, option value, variants, bundle switch | rebuild on the next run or button press | same | same |

Today only `latest` reacts to APKMirror and auto/pinned react only to tag changes (`build.py:355-360`); config edits never trigger a rebuild. The table above is the new rule.

Known limit (ponytail ceiling): with `allow_nearest` on, the fingerprint holds the wanted version, not the downloaded one; when APKMirror later lists the exact version, nothing rebuilds until another input changes or the user presses Rebuild. Fix when it matters: add the downloaded version to the comparison for near-mode items.

First run after 010: builds before 010 have `inputs_sha` NULL, so every enabled app rebuilds once (YouTube, Reddit). Accepted.

### 4.4 Results

Unchanged: markers (`build.py:27-60`), `failed_patches` into `build_jobs.failed_patches` (`build.py:108-109`), `result.json` validation, artifacts copy, retention, `build.succeeded/failed` events. `_require_build_on` (`build.py:368`) becomes "app disabled" → 409 for manual Rebuild of a disabled app.

### 4.5 Module layout

- New `w/apps.py` (owner of the enabled-app model): item rows; the functions part 3 calls — `enable(package, bundle_id, overrides=None)`, `disable(app_id)`, `get(app_id)`/`list()` (fields of §2.1 + `project_ids` from `item_dependency` + status from `build.item_status`), `update_settings(app_id, fields)` (version mode/pin/nearest, overrides, variants, engine options, dependencies, display name), `check_update(app_id)`, `rebuild(app_id)` (409 when disabled or without a download URL), `get_schedule()`/`put_schedule(spec, enabled, notify)`; patches and version validation moved from `catalog.py:597-703` unchanged in logic; `job_input`, `want_version`, `inputs`, the update-run handler (§5). Registered in `server.MODULES`.
- `w/catalog.py` becomes part 1's catalog layer.
- `w/build.py` keeps the build job, artifacts, cleanup, status; `needs_build` uses `apps.inputs`.
- `w/upstream.py` keeps GitHub check/pull/inspect; loses `item_update_jobs`, `post_item_update`, the `rebuild/pressed` branch of `pull` (`upstream.py:442-450`), `post_check`, `post_pull`, `list_upstreams` (routes go per explore §4; upstream rows become internal). `pull(upstream_id, log, job_id)` returns whether a new tag was downloaded.

### 4.6 Enable (requirements §3: immediate first fetch + build)

`apps.enable(package, bundle_id, overrides=None)`: `catalog.ensure_upstream(bundle_id)` (409 for GitLab bundles), then in one transaction `INSERT OR IGNORE INTO app(package)`, insert or update the `item` row (new row: `default_variants()`, engine-default `engine_options`, mode auto), `enabled=1`; then `jobs.enqueue('upstream', {'item_id': id}, 'manual')`. That job (§5 with scope one app) pulls the bundle + CLI + dependency projects, inspects, refreshes APKMirror versions when the mode is latest, then enqueues the build because `needs_build` is true for a never-built app. No download URL → the job runs part 3's locator; if it finds nothing the build is not enqueued and `apkm_state` tells the UI to ask for the listing URL; once the user pastes it, part 3 calls `check_update(app_id)`.
Changing the bundle calls the same function. `apps.disable(item_id)` sets `enabled=0` and nothing else.

## 5. The update run (global schedule, check-for-update, enable)

Job kind `upstream` (existing worker, `jobs.py:307`), handler dispatch on target:
- `{upstream_id}` → `upstream.pull` (Follow projects, OTA project pulls) — unchanged.
- `{all: true}` (scheduled, `trigger='schedule'`) or `{item_id}` (manual check, enable) → `apps.run_update(job, log)`:

1. `{all}` only: `catalog.refresh(log)` (part 1). It applies what succeeded and then raises `CatalogRefreshFailed` (or `CatalogRefreshBusy` when a manual refresh holds the lock); the run logs `[-] catalog refresh: <error>`, remembers the failure, continues (bundle releases come from GitHub, not from the catalog).
2. Items in scope: `{all}` → every `enabled=1` item; `{item_id}` → that item, which must be enabled (else `JobFailed("app <id> is disabled")`).
3. Upstreams in scope: `bundle.upstream_id` and `catalog.engine_cli(bundle.engine)` of those items, plus the upstreams of their dependency projects; `{all}` also adds every Follow project (today's daily schedule targets all projects, `scheduler.py:146`). For each, sorted, `upstream.pull` (check + download + prune + inspect, `upstream.py:423-440`); a per-row failure is stored in `upstream.check_error` and the loop continues; `TokenRejected`/`RateLimited` stop the GitHub step (as `check_all`, `upstream.py:182-188`).
4. APKMirror, sequentially per in-scope item: (a) no `apkmirror_list_url` and no `apkpure_url` → part 3's `apklocate.locate(package, log, cancel_event)` (search + exact package match, rate-limited, writes `apkm_*` state and the URLs, raises only on cancel); still no URL → skip the item (its `apkm_state` shows why) and do not build it. (b) `version_mode='latest'` with a list URL → the version check (the body of `build.appcheck_handler`, `build.py:165-187`, moved into `apps.check_versions(package, log, cancel_event)`), one list-page request per package. Success → `app.apkmirror_versions`, `apkm_error = NULL`; failure → `apkm_error = <reason>`, continue. Items in auto/pinned mode are not checked: a new APKMirror version cannot change their build (§4.3). The normal build still refreshes the list once a day (`versions_due`, `catalog.py:698-702`), so the pin list in the UI stays fresh.
5. Builds: for each in-scope item with `needs_build` → `jobs.enqueue('build', {'item_id': id}, job.trigger, job.schedule_id, {'notify': notify})`. Builds run in the build worker while the next run waits for nothing (no polling).
6. Notify: existing events only — `upstream.new_version` (once per tag, `upstream.py:163-166`) and `build.succeeded/failed`; `notify=false` on the schedule suppresses them through `jobs.options.notify` (`notify.py:54-57`).
7. The job ends `failed` with a reason listing failed steps (catalog, N upstreams, M version checks) when any step failed, else `success`. Builds already enqueued run either way.

Scheduler (`scheduler.py`): `OPTION_KEYS = {'update': {'notify': bool}, 'ota': {...}}`; `TARGET_KEYS['update'] = ()` with target fixed `{}`; `fire` for `update` → `jobs.enqueue('upstream', {'all': True}, 'schedule', sid, {'notify': o['notify']})`. `_app_jobs` and the `app` branches go. `jobs.OPTION_DEFAULTS` (`jobs.py:16`): drop `appcheck`, keep `upstream {'notify': True}` (drop `rebuild`).

Rate: one scheduled run = 1 catalog request + 1 GitHub request per distinct upstream + 1 APKMirror list page per latest-mode app, all serial in one thread. The APKMirror search for unknown listings is part 3 and is not part of the scheduled run.

## 6. Removals

Code (file:line at HEAD `991710b`):

| What | Where |
|---|---|
| Groups | `catalog.py:13` COLOR_RE, `:15-20` grp join + ORDER_SQL, `:37-44` enabled_group_items, `:70-176` group helpers and routes, `:432` _group_id, item routes' group_id branches; `build.py:419-457` group zip + `zipfile`/`time` imports if unused; `build.py:466` route; `ota.py:27-28` grp join/order; seed `groups` |
| Source × item | `catalog.py:249-258` engine-of-source/item (engine now from bundle), `source.keystore` / `source.cli_upstream_id` (constants + `engine_cli`), `:305-312` xposed_source_rules, `:319-322` check_version_mode, `:343-430` source helpers and routes, `:441` _default_key, `:446-575` item routes (rewritten in apps.py for the new row), `ITEM_SQL`/`_item_obj` (`:15-68`); `upstream.py:174-179` source_ids in _all_rows, `:191-197` list/check routes, `:462-469` post_pull, `:470-505` item_update_jobs + post_item_update, `:442-450` rebuild/pressed; `build.py:89` artifact_name args |
| Build/Update switches | `item.auto_update` (`schema/006:5`), `catalog.put_item` flag loop (`:513-517`), `pressed` (`upstream.py:423,454-455,488-491`, `build.py:191`), `build.appcheck_handler` (`build.py:165-198`, body moved), `appcheck` register in `build.register` (`build.py:219-222`) |
| Multi-target schedules | `scheduler.py:14-18` app keys, `:75-105` app branches, `:142-159` _app_jobs, `:165-166` fire app branch |
| Seed | `w/catalog-seed.json`; `catalog.py:816-881` (SEED_PATH, SEED_TABLES, JSON_COLS, BOOL_COLS, DAILY_SCHEDULE, _to_db, _from_db, seed_import, export_seed, seed_text); `__main__.py:34-35` seed_import call, `:54-57` export-seed command and its USAGE line; `README.md:11` and `:32` (seed/export-seed sentences); `docs/Frequently Asked Questions.md:23-25` |
| Version in shell | `utils.sh:45-57` detect_version, calls `utils.sh:168,425`, `build-item.sh:50` comment, `:54-75` mode case; `build.py:16` `version_unresolved` |
| Orphan data | `source`, `grp` tables; `upstream` rows referenced by no bundle/project (22 in the requirements); `bundle_info` rows of other tags than each row's `downloaded_tag`; files: `artifacts/<id>` of deleted items, `upstream/<id>` of deleted rows, `apk-cache/<package>` of packages without an enabled item |

Orphan files: SQL cannot delete files, so `build._cleanup_plan` (`build.py:258-283`) gains two entries — `artifacts/<id>` dirs with no `item` row, `upstream/<id>` dirs with no `upstream` row — next to its existing "apk-cache of packages no enabled item uses" (`:275-282`). The user presses Clean up once after the migration (existing route `POST /api/cleanup`). `upstream._prune` (`upstream.py:372-384`) additionally deletes `bundle_info` rows of pruned tags so they stop accumulating (part 1 owns upstream; noted in Interfaces).

Tests (`w/tests/`):

| File | Verdict |
|---|---|
| `test_catalog_seed.py`, `test_build_zip.py` | delete |
| `test_catalog_store.py` | delete groups/sources/items/flags tests; patches + version tests move to a `test_apps.py` against the new row |
| `test_catalog_upstream.py` | keep check/pull/download/prune/inspect tests; delete `auto_update`, `pressed`, `item_update_jobs`, schedule fan-out tests |
| `test_catalog_xposed.py` | delete source rules; keep item engine_options/variant rules re-pointed at a module bundle |
| `test_build_service.py` | delete appcheck-rebuild and old `needs_build` tests; keep markers, artifacts, retention, isolation, cleanup (fixture rewrite) |
| `test_core_scheduler.py` | delete app-kind target/option tests; keep next_run math and ota kind |
| `test_core_db.py:64` (`test_old_schedule_rows_become_app_rows`) | delete (tests 007, superseded by 010) |
| `test_core_startup.py:15,25` | drop the seed_import step from the expected boot order |
| `catalog_rows.py`, `device_helpers.py` | rewrite fixtures: one bundle + items, no groups/sources |
| `test_device_*` | fixtures only (items stay item-keyed) |
| `src/build/test-build-item.sh:88-90` `runner_version_unresolved` | delete; other cases change `.version` to `{value, near}` |

## 7. Migration `010_marketplace.sql`

One file, the established `PRAGMA foreign_keys=OFF; BEGIN; … COMMIT; PRAGMA foreign_keys=ON;` pattern (`schema/006`, `008`). Part 1's `bundle` DDL and part 3's `app` columns go into the same file, before the `item` rebuild. Because foreign keys are off, cascades do not fire: every orphan delete is explicit.

Order:
1. Part 1's DDL (`bundle`, `bundle_app`, `bundle_patch`, `store_app`, `catalog_feed`, part-1 doc §2). Insert the built-in bundle on the existing upstream row of `MorpheApp/morphe-patches` stable (matched by repo and channel, never by id): `INSERT INTO bundle(origin, host, repo, engine, name, upstream_id) SELECT 'builtin', 'github', repo, 'morphe', 'Morphe', id FROM upstream WHERE repo = 'MorpheApp/morphe-patches' AND channel = 'stable'`. On a fresh DB (no upstream rows) part 1 creates the built-in bundle and its upstream at boot. Part 3's `app` columns; `UPDATE app SET apkm_state = 'manual' WHERE apkmirror_list_url IS NOT NULL OR apkpure_url IS NOT NULL`. Drop `app.display_name`, `app.icon_file`, `app.icon_fetched_at` (`ALTER TABLE … DROP COLUMN`).
2. `item_new` as §2.1; copy items of the built-in bundle's source, keeping ids:
   `INSERT INTO item_new(id, package, bundle_id, display_name, enabled, version_mode, version_pin, allow_nearest, variants, patch_overrides, engine_options) SELECT i.id, i.package, b.id, i.display_name, i.enabled, i.version_mode, CASE WHEN i.version_mode = 'pinned' THEN i.version_pin END, i.allow_nearest, i.variants, i.patch_overrides, i.engine_options FROM item i JOIN source s ON s.id = i.source_id JOIN bundle b ON b.upstream_id = s.upstream_id AND b.origin = 'builtin';`
   `UNIQUE(package)`: if several source rows share the built-in upstream, only the lowest item id per package is copied (`AND i.id = (SELECT min(i2.id) FROM item i2 JOIN source s2 ON s2.id = i2.source_id WHERE s2.upstream_id = s.upstream_id AND i2.package = i.package)`); the others count as dropped items in step 3.
   Live DB after the cleanup (`.claude/tmp/be-apps/live-db-after-cleanup.txt`): one source `morphe` (upstream 2, CLI 1) with items 1 YouTube (enabled), 2 Reddit (enabled), 3 YT Music (disabled). Result: all three rows kept with their ids, variants, overrides `{}` and mode auto; YT Music stays `enabled=0` so its settings survive; nothing is dropped.
3. Explicit orphan deletes for dropped items: `item_dependency`, `device_assignments`, `ota_installs` (kind item), `artifacts`, `build_jobs`, then `jobs` of kind build whose `target.item_id` is gone (artifacts first: `artifacts.job_id` is RESTRICT).
4. `DROP TABLE item; ALTER TABLE item_new RENAME TO item;` `DROP TABLE source; DROP TABLE grp;`
5. `ALTER TABLE build_jobs ADD COLUMN inputs_sha TEXT;`
6. Schedules: rebuild with `kind IN ('update','ota')`; keep `ota` rows; insert one `update` row reusing the id, spec, enabled, last/next run of the lowest-id `app` row with `options.update = true` (live DB: "Daily upstream", daily 09:00, enabled, `next_run_at` set), and options `{"notify": <old notify>}`. When no such row exists (fresh DB), the migration inserts nothing: `tick` never fires a row with NULL `next_run_at` (`scheduler.py:180-182`), so the row is created at boot by `scheduler.ensure_update()` (in place of the removed `seed_import` call, `__main__.py:34-35`): if no `update` row exists, `create('Update', 'update', {}, {every: day, at: '09:00'}, {notify: true})`, which computes `next_run_at`. Old `jobs.schedule_id` pointing at dropped rows → NULL. Partial unique index.
7. Jobs: delete `appcheck` rows; rebuild `jobs` with `kind IN ('build','upstream','ota')` (pattern `schema/006:6-21`).
8. Orphan `upstream` rows: delete rows not referenced by `bundle.upstream_id` or `project.upstream_id` and not one of the three CLI rows of `catalog.ENGINE_CLI` (matched by repo/channel/tag: `MorpheApp/morphe-desktop` stable, `JingMatrix/LSPatch` stable, `7723mod/NPatch` tag v1.0.5; never by id); their `bundle_info` rows explicitly (FKs are off). Delete `bundle_info` rows whose tag ≠ their row's `downloaded_tag`.
9. Seed URLs: `INSERT OR IGNORE INTO app(package, apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url, apkm_state) VALUES (…, 'manual') …` — the 41 rows generated once from `catalog-seed.json` `apps` before the file is deleted. Existing rows (the live DB already has these 41) are untouched.

Verification of the migration (one-off, scratch, not committed): copy the live DB with `sqlite3 local/data/webui.db ".backup .claude/tmp/be-apps/migrate-check.db"` (online backup API: the copy is consistent while the server runs and the live file is not modified; run by the lead or the user, not by a designer), run `db.migrate()` on the copy, then check `PRAGMA foreign_key_check` is empty, items 1/2/3 kept (3 disabled), `item_dependency` = (1,1), (3,1), every `device_assignments` row kept, 41 `app` rows with URLs, one `update` schedule at daily 09:00, only the built-in bundle's upstream, CLI rows and Follow-project upstreams left. Every rule in 010 matches rows by content (repo, channel, kind, foreign keys), never by a fixed id, so it also runs on any other DB built by 001–009.


## 8. Tests worth committing (≤1 s each, hermetic, in-memory/tmp DB)

Core logic and data integrity only (project test policy):
1. Migration 010 on a fixture DB built by 001–009 with: built-in + one other source, three built-in items (one disabled, one with overrides and a pin, one with a dependency and a device assignment), one other-source item with artifact/assignment/receipt, an `app` schedule, an `appcheck` job. Asserts: ids kept, other item and all its rows gone, `foreign_key_check` empty, one `update` schedule with the old spec, no appcheck jobs, orphan upstream gone, seed URL rows present. Replaces `test_core_db.py:64`.
2. `want_version`: pinned; auto = max of union by `version_key`; auto with empty union = null; latest = null in `job.json`.
3. `needs_build` matrix: never built; last failed; artifacts gone; each input change (tag, CLI tag, pin, override, bundle switch, latest-mode new APKMirror version) flips it; auto-mode new APKMirror version does not.
4. Update run with fake refresh/upstream/locate/check functions: catalog failure does not stop builds; disabled items untouched; app without URL after locate is not built; failed version check sets `apkm_error`; job ends failed with reason; only `needs_build` items enqueued.
5. Enable: new row gets defaults and one scoped job; re-enable after disable keeps overrides; bundle change keeps same-name overrides.
6. Scheduler: `update` row cannot be created or deleted; fire enqueues one `{all:true}` job.
7. `job.json` shape: no `mode`, `version.value` present; shell leg `test-build-item.sh` keeps its runner cases with the new field.

Not committed: the live-copy migration run (§7, scratch), UI wiring.

## Interfaces

Agreed with part 1 (be-catalog, `docs/superpowers/specs/parts/2026-10-26-mp-backend-catalog.md` "Interfaces") and part 3 (be-api, `.claude/tmp/be-api/interfaces-proposal.md` "From be-apps") on 2026-10-26.

Consumed from part 1:
- `bundle(id, origin, host, repo, branch, engine, name, …, listed, upstream_id)`; every catalog bundle has a row; a chosen bundle dropped from the catalog stays with `listed=0`. `item.bundle_id` FKs it.
- `catalog.ensure_upstream(bundle_id) -> upstream_id` (on enable / bundle change; 409 for GitLab).
- `catalog.engine_cli(engine) -> upstream_id`; the keystore is this part's constant per engine.
- `catalog.patch_list(bundle_id, package) -> (tag, files, patches | None)` from `bundle_info` at the downloaded tag.
- `catalog.refresh(log)` raises `CatalogRefreshFailed` / `CatalogRefreshBusy` after applying what succeeded.
- `upstream._packages` reads `item JOIN bundle`; `upstream._prune` deletes `bundle_info` of pruned tags.
- Names/icons in `store_app`; label = `item.display_name` or `store_app.name` or package.
- This part owns the 010 SQL and merges part 1's DDL into it.

Provided to part 3:
- Key `item.id` (part 3 may call it `app_id` on the wire), `UNIQUE(package)`.
- `apps.enable/disable/get/list/update_settings/check_update/rebuild/get_schedule/put_schedule` (§4.5).
- No `appcheck` job kind: locate and the version check are steps of the update job (§5 step 4); part 3 provides `apklocate.locate(package, log, cancel_event)` that writes the `apkm_*` columns and the URLs and raises only on cancel.
- Build reads `app.apkmirror_list_url` / `apkpure_url`; the `apkm_*` columns are added in 010; version-check failures share `apkm_error`.
- One `update` schedule row plus `ota` rows.

## Open questions for the user

None.

## Uncertainties

- Opening the backup read-only touched its existing `webui.db.bak-2026-10-26-shm` (SQLite shared-memory file, mtime updated); the backup file itself is unchanged (mtime 11:49).
