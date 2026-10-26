# Marketplace backend — APKMirror auto-locate and HTTP API

Part 3 of 3 of the Marketplace backend design (catalog layer = be-catalog, enabled-app model and build flow = be-apps). Binding input: `docs/superpowers/specs/2026-10-26-marketplace-requirements.md` (cited as "req §n"). Code refs are at HEAD `991710b`; `w/` = `local/webui/`.

## 1. APKMirror auto-locate

### 1.1 What the pages show (verified 2026-10-26, 3 live requests)

| Page | Evidence | Use |
|---|---|---|
| Search `https://www.apkmirror.com/?post_type=app_release&searchtype=app&s=<package>` | 200, no challenge (research doc §4). Rows `div.appRow` → `h5.appRowTitle a` with `href="/apk/<org>/<slug>/"`. **Rows carry no package name**; the package only appears in the page title/form. For `com.google.android.youtube` the page has 10 app links (official, TV variants, `ogmods/ogyoutube-3`, …). | Candidate list, fuzzy |
| App page `https://www.apkmirror.com/apk/google-inc/youtube/` | 200. `<a … alt="View on Play Store" href="https://play.google.com/store/apps/details?id=com.google.android.youtube">`; link `uploads/?appcategory=youtube` ("See more uploads"). | Verify: Play id |
| App page `https://www.apkmirror.com/apk/ogmods/ogyoutube-3/` (a mod) | 200. **Same Play link `id=com.google.android.youtube`**; no `appcategory` link. | Shows the Play id alone is not unique |
| Release page `…/ogyoutube-3-4-4-11-release/` | 302 (not followed; request budget used up). | Not usable as evidence |

Saved copies (scratch, gitignored): `.claude/tmp/research-official/a1.html` (search), `.claude/tmp/be-api/app-youtube.html`, `.claude/tmp/be-api/app-ogyt.html`. The implementer trims them into committed fixtures (§5) and must not delete them before that.

Listing URL format used by builds today: `https://www.apkmirror.com/uploads/?appcategory=<slug>` (all 40 seed URLs, `w/catalog-seed.json`; consumed by `get_apk`, `src/build/utils.sh:146,195,229-231`, and `check-versions.sh`). The locator derives it from the candidate slug: `/apk/<org>/<slug>/` → `…/uploads/?appcategory=<slug>`.

### 1.2 Algorithm (`w/apklocate.py`, new)

```
locate(package, name) -> Result(state, list_url|None, candidates[], error|None)
1. html = fetch(search_url(package))                       # 1 request
2. cands = parse_search(html)                              # [(org, slug, title)] in page order, de-duplicated
   order: title equal to catalog store name (case-insensitive) first, then page order; keep first MAX_CANDIDATES = 5
3. for each cand: page = fetch(app_url(cand)); if play_id(page) == package: matches.append(cand)   # ≤ 5 requests
4. 0 matches -> not_found | 1 match -> found, list_url = uploads URL of the match | ≥ 2 -> ambiguous, candidates = matches
```

- Exact-match rule (req §4): a candidate counts only when the `View on Play Store` link id equals the package exactly. Apps without a Play link never match → not_found → manual URL.
- Ambiguous is the expected outcome for popular apps with mods on APKMirror (YouTube). No tie-break heuristic: the UI shows the matched candidates and the user picks one with one click (= manual URL, §1.5). ponytail: no release-page check; add one only if ambiguous turns out frequent, and capture a fixture first.
- Search is by package only (req §4). The catalog name is only used to rank candidates.
- Parsing: stdlib `html.parser` in the server process, on text written by the isolated fetch (below). No foreign code runs in the server; the parser only reads attributes.

### 1.3 Fetch and the solver chain

- New `src/build/fetch-page.sh <url> <out>`: sources `utils.sh`, calls `_cf_get "$1"` (FlareSolverr → CFBypass, `utils.sh:59-131`), writes `$html` to `<out>`, exits 1 when the chain fails. Run with `jobs.run_isolated(log, [...], env_keys=build.SOLVER_ENV, timeout=FETCH_TIMEOUT)` (pattern of `build.appcheck_handler`, `w/build.py:165-186`); the result is read with `jobs.open_runner_file`.
- `FETCH_TIMEOUT = 120` s per page (one solver round incl. retries).
- Blocked = runner exit ≠ 0, or the page has a Cloudflare challenge marker (`<title>Just a moment` or `cf-chl` / `challenge-platform`), or HTTP 403/429 when the solver reports it. Parser mismatch (search page without `div.appRow` and no "no results" text, app page without the Play-link block) = `failed`, with the URL and what was missing in the error.

### 1.4 Rate limit, cooldown, caching

All in `apklocate.py`, module state with an injectable clock (precedent: `auth._clock`, `w/auth.py:29`):

```python
_clock = time.monotonic          # tests replace
_wait = None                     # set per job to job.cancel_event.wait (returns early on cancel)
MIN_INTERVAL = 10                # s between two APKMirror lookup requests (global)
BLOCK_COOLDOWN = 3600            # s: after a block, no lookup request at all
NOT_FOUND_RETRY = 7 * 86400      # s: not_found / failed are retried by the schedule after this
```

- Every lookup request (search, app page, and the version check of `check-versions.sh`) goes through `throttle()`: wait until `last + MIN_INTERVAL`, then record. Rate limiting is time as business data, so the wait is allowed; it is cancellable via the job's cancel event.
- Scope: all lookups run inside be-apps' scoped `upstream` job (one worker thread per job kind, `w/jobs.py:307-310`); the limiter still takes a `threading.Lock` so a second caller cannot race it. ponytail: builds (`build-item.sh`) are not throttled by it — they already run one at a time; add a shared limiter if blocks show up during builds.
- After a block: `blocked_until = now + BLOCK_COOLDOWN` (in memory; a restart resets it). Lookups during the cooldown make zero requests and set the app to `blocked` with `retry_at = blocked_until`.
- Cache = the `app` row (per package, outlives enable/disable). Columns, added in be-apps' migration 010: `apkm_state TEXT NOT NULL DEFAULT 'pending' CHECK (apkm_state IN ('pending','found','manual','not_found','ambiguous','blocked','failed'))`, `apkm_candidates TEXT` (JSON list), `apkm_checked_at TEXT`, `apkm_retry_at TEXT`, and one shared `apkm_error TEXT` for the last lookup or version-check error. Migrated/seed rows with a list URL → `manual`.

| state | meaning | automatic retry |
|---|---|---|
| `pending` | not looked up yet / lookup queued | next scoped update job |
| `found` | auto-located, `apkmirror_list_url` set | never re-located |
| `manual` | user URL or migrated seed URL | never re-located |
| `not_found` | 0 exact matches | schedule run after `retry_at` (7 d) |
| `ambiguous` | ≥ 2 exact matches; `apkm_candidates` filled | none — user picks |
| `blocked` | solver chain failed / challenge / 403 | after `retry_at` (cooldown end) |
| `failed` | page shape not recognised | schedule run after `retry_at` (7 d) |

- Manual retry `POST /api/apps/{id}/locate` ignores `retry_at` for `not_found|failed|ambiguous`, but not the block cooldown (409 with the end time).

### 1.5 When it runs, and the manual path

- Only for enabled apps (req §4), never for the whole catalog. Trigger = be-apps' scoped `upstream` job with target `{item_id}` (pull bundle + CLI + dependency projects, inspect, APKMirror step, then enqueue the build). Its APKMirror step calls `apklocate.locate(package, log, cancel_event)` (writes `apkm_*`, raises only on cancel): if state ∉ {found, manual} and due → lookup; then, if a list URL exists → version check as today (`w/build.py:165-198`). No new job kind, so no `jobs` table rebuild. Enqueued scoped `{item_id}` by `apps.enable` and `apps.check_update`, as `{all: true}` by the global schedule run (which skips apps without a download URL), and by `POST /api/apps/{id}/locate`.
- Enable runs the first build right after the locate (req §3). If the lookup ends without a URL, the scoped job does not enqueue the build and the app shows `download.state` with the reason; nothing guesses a URL. The build's own `apk_not_listed` failure (be-apps) stays as the backstop.
- Manual URL: `PUT /api/apps/{id}` with `download.apkmirror_list_url`. Accepted forms: `https://www.apkmirror.com/uploads/?appcategory=<slug>` or `https://www.apkmirror.com/apk/<org>/<slug>/` (normalised to the uploads form). Other hosts/paths → 400. Sets state `manual`, clears candidates/error. Picking an ambiguous candidate = the same call with that candidate's `list_url`. Setting it to `null` → state `pending` and enqueues a lookup. APKPure URL optional (existing `apkpure_url`, `w/catalog.py:185`).
- Build guard: a build is refused (409 `needs_download_url`) when a variant's download kind has no URL (`apkmirror` → list URL, `apkpure` → `apkpure_url`, `url` → its url).

## 2. HTTP API

Conventions: JSON only; auth as today (`w/auth.py:17-21`, `w/server.py:203-211`): session cookie or device bearer token; same-origin + JSON content type on mutations. **A** = used by Android (req §6 scope: Marketplace, Apps, Schedules), **W** = web. All Android routes accept a device token (none is in `SESSION_ONLY`).

Enabled apps are addressed by integer `id` = `item.id` (be-apps keeps the table name `item` and the ids of migrated rows, so artifacts, device assignments, OTA receipts, `jobs.target.item_id` and the Android plan keep meaning). Marketplace entries and icons are addressed by package. Bundles are referenced by the integer `bundle.id` (be-catalog) as `bundle_id` in bodies. The router matches each route by regex (`w/server.py:126-127`), so `/api/apps/{id}/…` and `/api/apps/{pkg}/icon` coexist.

### 2.1 Marketplace (W, A)

`GET /api/marketplace?q=&category=&origin=&enabled=&sort=&page=&per_page=`
- `q`: case-insensitive substring of name or package. `category`: exact store category. `origin`: `official|builtin|custom`. `enabled`: `true|false`. `sort`: `name` (default) | `bundles` (bundle count desc) | `updated` (newest bundle update desc). `page` ≥ 1 (default 1), `per_page` 1–100 (default 50). Unknown values → 400.
- Filtering/sorting in memory over `catalog.list_apps()` (~1200 rows). ponytail: in-memory; move to SQL if the catalog grows past ~10k apps.
```json
{"total": 1207, "page": 1, "per_page": 50,
 "categories": [{"name": "Social", "count": 88}],
 "apps": [{"package": "com.reddit.frontpage", "name": "Reddit", "category": "Social", "icon_url": "/api/apps/com.reddit.frontpage/icon",
           "bundle_count": 4, "origins": ["official", "builtin"], "app_id": 12, "enabled": true}]}
```
`categories` counts are for the current filter minus `category`; `icon_url` is the icon route when `has_icon`, else `null`; `app_id` is the `item` row id or `null` (a disabled app keeps its row, `enabled: false`). Description is on the detail only.

`GET /api/marketplace/{pkg}` → 404 when the package is in no catalog.
```json
{"package": "…", "name": "…", "category": "…", "description": "…", "icon_url": "/api/apps/<pkg>/icon", "app_id": null, "enabled": false,
 "download": {"state": "pending", "...": "see 2.2"},
 "bundles": [{"bundle_id": 1, "name": "Morphe Patches", "author": "MorpheApp", "host": "github",
              "repo": "MorpheApp/morphe-patches", "origin": "builtin", "engine": "morphe", "stars": 1200,
              "listed": true, "selectable": true, "not_selectable_reason": null,
              "updated_at": "2026-10-20T…Z", "versions": ["20.40.45", "…"],
              "patches": [{"name": "Hide ads", "description": "…", "default_enabled": true}]}]}
```
`download` is present only when an `app` row exists for the package. Module bundles (`engine` lspatch/npatch) have `patches: []`. `default_enabled` may be `null` (catalog lacks it for some patches; be-catalog uncertainty) → UI shows "unknown". Shape = `catalog.app_bundles(package)` as-is; its `KeyError` for an unknown package → 404.

### 2.2 Apps (W, A)

App object (list and detail):
```json
{"id": 12, "package": "com.reddit.frontpage", "name": "Reddit", "icon_url": "…",
 "bundle": {"bundle_id": 1, "name": "…", "origin": "builtin", "engine": "morphe", "tag": "v1.48.0"},
 "app_version": "2026.40.0", "status": {"state": "up_to_date|update_available|building|queued|failed|not_built|needs_download_url",
   "job_id": 345, "progress": {"step": "patch", "index": 4, "count": 6}, "fail_code": null, "fail_detail": null},
 "download": {"state": "found", "apkmirror_list_url": "https://www.apkmirror.com/uploads/?appcategory=reddit",
   "apkmirror_example_url": null, "apkmirror_example_url2": null, "apkpure_url": null,
   "candidates": [{"title": "YouTube", "url": "https://www.apkmirror.com/apk/google-inc/youtube/",
                   "list_url": "https://www.apkmirror.com/uploads/?appcategory=youtube"}],
   "error": null, "retry_at": null}}
```
`status` values come from be-apps (`build.item_status` today); `needs_download_url` is derived from §1.5.

| Route | Body / result | Errors |
|---|---|---|
| `GET /api/apps` (W, A) | `{"apps": [app…]}` rows with `enabled = 1` only | — |
| `POST /api/apps` (W, A) enable | `{"package", "bundle_id", "patches"?: {name: bool}}` → 201 app via `apps.enable(package, bundle_id)` (re-enables a disabled row with its settings); enqueues the scoped update job (locate + first build) | 404 unknown package/bundle; 409 `bundle_unsupported` (bundle does not list the package); 409 `bundle_not_selectable` (e.g. GitLab, reason from `app_bundles`); 409 `already_enabled` |
| `GET /api/apps/{id}` (W, A) | app + `settings`: `{"version": {"mode": "auto|pinned|latest", "pin", "allow_nearest", "supported": [], "apkmirror": []}, "project_ids": [], "variants": [], "engine_options": null}` | 404 |
| `PUT /api/apps/{id}` (W, A) | partial: `bundle_id`, `version{mode,pin,allow_nearest}`, `project_ids`, `variants`, `engine_options`, `download{apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url}` → 200 app. Changing `bundle_id` keeps overrides whose patch names exist in the new bundle; stale ones are reported (`stale_overrides`) not dropped silently | 400 field validation; 409 `bundle_unsupported` |
| `DELETE /api/apps/{id}` (W, A) disable | 204 via `apps.disable(item_id)`: `enabled = 0`, row and settings kept | 404 |
| `GET /api/apps/{id}/patches` (W, A) | today's shape of `get_patches` (`w/catalog.py:597`): patches from the downloaded bundle incl. options, overrides, stale list | 409 module bundle |
| `PUT /api/apps/{id}/patches` (W, A) | today's `put_patches` body (`w/catalog.py:635`) | 400/409 as today |
| `POST /api/apps/{id}/check` (W, A) | check for update = the scoped update job → `{"job_id"}` | 404 |
| `POST /api/apps/{id}/build` (W, A) | rebuild: enqueue `build` directly → `{"job_id"}` | 409 `needs_download_url`, 409 `upstream_missing` |
| `POST /api/apps/{id}/locate` (W, A) | re-run auto-locate → `{"job_id"}` | 409 `apkmirror_blocked` (with `retry_at`), 409 when state is `found|manual` |
| `GET /api/apps/{id}/artifacts` (W, A) | today's `list_item_artifacts` shape (`w/build.py:399`) | 404 |
| `GET /api/artifacts/{id}/download` (W, A) | unchanged (`w/build.py:405`) | unchanged |
| `GET /api/apps/{pkg}/icon`, `POST /api/apps/{pkg}/icon/refresh` (W, A) | paths unchanged (`w/catalog.py:905-906`); any catalog package; be-catalog proxies the store `iconUrl` | 404 |

### 2.3 Schedule (W, A for both)

| Route | Body / result |
|---|---|
| `GET /api/schedule` | the single `schedules` row of kind `update` (be-apps): `{"spec": {"every": "hours", "hours": 6}, "enabled": true, "options": {"notify": true}, "last_run_at", "next_run_at"}` |
| `PUT /api/schedule` | `{"spec"?, "enabled"?, "options"?: {"notify"}}`; no create/delete; `spec` validated by `scheduler._check_spec` (`w/scheduler.py:61`) |
| `POST /api/schedule/run` | run now → 202 |
| `GET/POST /api/schedules`, `PUT/DELETE /api/schedules/{id}`, `POST /api/schedules/{id}/run`, `POST /api/schedules/preview` | OTA schedules only (`kind` must be `ota`; the `update` row is not listed or editable here). Same handlers (`w/scheduler.py:265-270`) minus the app branch |

### 2.4 Catalog and custom sources (W only)

| Route | Body / result |
|---|---|
| `GET /api/catalog/status` | `catalog.refresh_status()` as-is: per feed `{last_ok_at, last_attempt_at, last_error, app_count, bundle_count, duplicates[]}`, per built-in/custom source `{repo, list_tag, last_error}` |
| `POST /api/catalog/refresh` | synchronous `catalog.refresh()` (30 s fetch timeout per feed) → 200 with the new status; `CatalogRefreshBusy` → 409 `catalog_refresh_busy`; `CatalogRefreshFailed` → 502 with the feed, URL and error; last good copy stays in use |
| `GET /api/sources` | `{"sources": catalog.list_custom()}` (custom only; built-in and official are not editable) |
| `POST /api/sources` | `{"url": "https://github.com/o/r", "engine": "morphe|lspatch|npatch", "asset_regex"?: str (module engines), "packages"?: [str] (module engines)}` → 201 `{"bundle_id"}` via `catalog.add_custom` |
| `DELETE /api/sources/{id}` | `id` = bundle id; 204 via `catalog.delete_custom`; 409 `source_in_use` listing the `item` rows that use it |

ponytail: no PUT for custom sources; delete and re-add. Add PUT when a field turns out to need frequent edits.

### 2.5 Unchanged routes kept

No re-key: ids are `item.id`. Jobs (`w/jobs.py:468-471`, filter `item_id`), devices (`w/devices.py:472-477`, assignments body `item_ids`), OTA (`w/ota.py:779-782`), `/api/me/*` (`w/ota.py:488-493`, plan step `kind: "item"`), projects (`w/projects.py:495-501`), auth (`w/auth.py:263-270`), settings/health (`w/settings.py:168-170`), notify (`w/notify.py:237-239`), cleanup (`w/build.py:462-463`, rewritten by be-apps).

## 3. Deleted or changed routes

| Route | Where | Fate |
|---|---|---|
| `GET /api/catalog` | `w/catalog.py:885` | delete → `/api/marketplace`, `/api/apps` |
| `GET/POST /api/groups`, `PUT /api/groups/order`, `PUT/DELETE /api/groups/{id}`, `PUT /api/groups/{id}/items/order` | `w/catalog.py:886-891` | delete |
| `GET /api/groups/{id}/download` | `w/build.py:466` | delete |
| `GET/POST /api/sources`, `PUT/DELETE /api/sources/{id}` | `w/catalog.py:892-895` | replaced by custom-source routes (§2.4), new handlers |
| `GET/POST /api/items`, `GET/PUT/DELETE /api/items/{id}` | `w/catalog.py:896-900` | → `/api/apps[/{id}]` |
| `PUT /api/apps/{pkg}` | `w/catalog.py:901` | delete; URL fields move into `PUT /api/apps/{id}` `download` |
| `GET/PUT /api/items/{id}/patches` | `w/catalog.py:902-903` | → `/api/apps/{id}/patches` |
| `PUT /api/items/{id}/version` | `w/catalog.py:904` | merged into `PUT /api/apps/{id}` `version` |
| `POST /api/items/{id}/build` | `w/build.py:460` | → `POST /api/apps/{id}/build` |
| `POST /api/builds` (batch) | `w/build.py:461` | delete (no batch action in req §6) |
| `GET /api/items/{id}/artifacts` | `w/build.py:464` | → `/api/apps/{id}/artifacts` |
| `POST /api/items/{id}/update` | `w/upstream.py:508` | → `POST /api/apps/{id}/check` |
| `GET /api/upstreams`, `POST /api/upstreams/check`, `POST /api/upstreams/{id}/pull` | `w/upstream.py:509-511` | delete (upstream rows become internal; Follow projects pull via their own route — be-apps/projects owner to confirm `projects.js`/`item.js` use of `…/pull`) |
| schedules `kind: "app"` | `w/scheduler.py:14-18,75-105,142-166` | delete; global schedule §2.3 |

## 4. Error shape

Unchanged envelope `{"error": "<message>"}` (`w/server.py:170-171`), plus an optional `"code"` only where a client branches on it: `needs_download_url`, `apkmirror_blocked`, `bundle_unsupported`, `bundle_not_selectable`, `already_enabled`, `source_in_use`, `catalog_refresh_busy`, `upstream_missing`. Extra fields allowed with a code: `retry_at` (blocked), `item_ids` (source_in_use).

Every new message names the affected object, the expected value and the actual value:
- `app com.reddit.frontpage: download.apkmirror_list_url expected https://www.apkmirror.com/uploads/?appcategory=<slug> or /apk/<org>/<slug>/, got 'https://apkpure.com/x'`
- `bundle 7 (github o/r): does not support package com.x (supports 3 packages: a, b, c)`
- `app 12 (com.x): build needs an APKMirror listing URL; lookup state is ambiguous (2 candidates)`
- `APKMirror lookup for com.x blocked until 2026-10-26T11:00:00Z: FlareSolverr and CFBypass both failed on <url>`
- `marketplace: per_page expected 1-100, got 500`

## 5. Tests worth committing (≤ 1 s each, hermetic)

Fixtures (trimmed to the needed rows, a few KB each) under `w/tests/fixtures/apkmirror/`: `search-youtube.html` (from `a1.html`), `app-youtube.html`, `app-ogyoutube.html`, `challenge.html` (hand-written minimal Cloudflare page), `search-empty.html` (hand-written).

| Test | Why it stays (CLAUDE.md test policy) |
|---|---|
| `parse_search` on the fixture → candidate list in page order, de-duplicated, only `/apk/<org>/<slug>/` | parser = core logic |
| `play_id` on both app fixtures; missing block → `failed` | exact-match rule (req §4) |
| `locate` with a fake fetch: 1 match → found + uploads URL; official + mod → ambiguous with 2 candidates; 0 → not_found; challenge / runner exit 1 → blocked; ≤ 5 app pages fetched | decision table = core logic, prevents silent wrong URL |
| limiter with fake clock + recording wait: second request waits `MIN_INTERVAL`; during cooldown zero fetch calls | rate limit protects the IP; past 403 incident |
| manual URL validation: both accepted forms normalise; other host → 400 with expected/actual | input validation at the boundary |
| marketplace: filter + sort + pagination over a 5-app fake catalog; invalid `per_page` → 400 | query logic |
| `POST /api/apps` 201; unsupported bundle → 409 `bundle_unsupported`; build without URL → 409 `needs_download_url` | data integrity |
| device token reaches the Android routes; `SESSION_ONLY` still 403 | security |

Not committed: live APKMirror requests, solver shell runs, UI rendering, a test per route that only forwards.

## Interfaces

Peer drafts: be-catalog `docs/superpowers/specs/parts/2026-10-26-mp-backend-catalog.md` §Interfaces; be-apps `.claude/tmp/be-apps/interface-proposal.md` "To be-api". All rows agreed by message 2026-10-26.

| From | What this part consumes |
|---|---|
| be-catalog | `bundle.id` integer = `bundle_id`; host and repo separate fields |
| be-catalog | `list_apps() -> [{package, name, category, description, has_icon, origins[], bundle_count, newest_version}]`; icons only via `GET /api/apps/{pkg}/icon` (no external URL leaves the server) |
| be-catalog | `app_bundles(package) -> [{bundle_id, origin, host, repo, name, author, stars, updated_at, engine, listed, selectable, versions[], patches[{name, description, default_enabled}]}]`; unknown package → `KeyError` |
| be-catalog | `refresh_status()`; `refresh()` synchronous, raises `CatalogRefreshBusy` / `CatalogRefreshFailed`; no new job kind |
| be-catalog | `list_custom()`, `add_custom(url, engine, asset_regex=None, packages=())` (regex + packages only for lspatch/npatch, no keystore field), `delete_custom(bundle_id)` |
| be-catalog | `store_app.name` for ranking search candidates |
| be-apps (`w/apps.py`) | key `item.id` (JSON `id`), old ids kept, UNIQUE package; no re-key of jobs/devices/OTA/Android |
| be-apps | `enable(package, bundle_id, overrides=None)`, `disable(app_id)` (row kept), `get/list` (status from `build.item_status`), `update_settings(app_id, fields)`, `check_update(app_id)`, `rebuild(app_id)` (409 without download URL), `get_schedule()/put_schedule(spec, enabled, notify)` |
| be-apps | `appcheck` job kind removed; lookup is a step of the `upstream` update job; 5 `apkm_*` columns in 010 |
| this part → be-apps | `apklocate.locate(package, log, cancel_event)`, `apklocate.validate_list_url(url) -> normalised url` |

Open between peers (non-blocking): `sort=updated` needs a per-app newest bundle `updated_at` in `list_apps()`, and a not-selectable reason in `app_bundles()`; asked be-catalog. Without them: drop `sort=updated`; the 409 says "bundle <id> (<host> <repo>) is not selectable: <host> releases are not supported".

## Open questions for the user

1. **Ambiguous lookup for popular apps.** APKMirror lists mods (e.g. OGYouTube) that point to the same Play Store package as the original app, so for apps like YouTube the automatic lookup finds two or more exact matches. Options: (a) show the matches and let the user pick one with one click (recommended: no guess, one click, done once per app); (b) prefer the candidate whose publisher name matches the catalog author / is "google-inc"-like (fewer clicks, but a heuristic that can pick a mod silently). Recommendation: (a).
2. **Lookup request budget.** One lookup costs up to 6 APKMirror requests (1 search + 5 app pages) with 10 s between requests, so enabling 10 apps at once takes about 10 minutes before the first build starts. Options: (a) keep 10 s (recommended: the past 403 incident); (b) 3 s (faster, more block risk). Recommendation: (a).
