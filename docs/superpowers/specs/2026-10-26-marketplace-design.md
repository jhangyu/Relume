# Marketplace refactor — merged design spec

Status: merged design, 2026-10-26. Approved design inputs, merged into one document for the implementation plan `docs/superpowers/plans/2026-10-26-marketplace.md`.
Code references are at HEAD `0d55fc3` unless noted; `w/` = `local/webui/`. Live database facts were read with `sqlite3 -readonly local/data/webui.db` on 2026-10-26.

## 0. How to read this spec

Inputs, in order of precedence (later wins):
1. `docs/superpowers/specs/2026-10-26-marketplace-requirements.md` (cited "req §n").
2. The three backend parts, cited "catalog part §n", "apps part §n", "API part §n":
   `docs/superpowers/specs/parts/2026-10-26-mp-backend-catalog.md`, `…-apps.md`, `…-api.md`.
3. `docs/logs/2026-10-26-app-icon-check.md` (cited "icon check G1–G5").
4. `docs/logs/2026-10-26-marketplace-decisions.md` (cited "decisions").
5. This document. **This document wins over all of the above.** Where it says "as catalog part §x", the part's text is binding as written, except for the amendments listed in that section of this document and in §12.

UI references: `docs/superpowers/mockups/marketplace/candidate-2/` — `web/` and root `icons.js` + `tokens.md` as of commit 0d55fc3 (web half); `android/` with `android/tokens.md`, `android/m3.css`, `android/icons-material.js` (Android half; the palette is referenced by token name, values come from `android/tokens.md`).

Glossary:
- **Enabled app** = one `item` row (the table keeps its name and its integer ids, apps part §1.1). `enabled = 1` shows it in the Apps tab; `enabled = 0` keeps the remembered choice.
- **Bundle** = one patch source (a GitHub repo with Morphe patches, or an Xposed module repo), one `bundle` row.
- **Origin** = `official` (community catalog), `builtin` (`MorpheApp/morphe-patches`), `custom` (added by the user).
- **Update run** = the job of kind `upstream` with target `{all: true}` (global schedule, "Check all") or `{item_id}` (enable, "Check for update", locate).

## 1. Module layout (server)

| Module | Owner of | Status |
|---|---|---|
| `w/catalog.py` | feeds, parsers, apply, `bundle*`/`store_app`/`catalog_feed` rows, built-in + custom sources, patch list lookup, icons; keeps `ABIS`, `display_name`, `version_key`, `upstream_row`, `set_apkmirror_versions`, `UpstreamMissing`, `default_variants`, `validate_variants`, `validate_engine_options`, `check_xposed_variants`, `is_module` | rewritten (catalog part §1–§6) |
| `w/apps.py` | enabled-app rows, settings, patches and version validation (moved from `catalog.py:597-703`), `job_input`, `want_version`, `inputs_sha`, the update run, the global schedule row | new (apps part §4.5) |
| `w/apklocate.py` | APKMirror search + exact match, rate limit, cooldown, `apkm_*` columns, download URL validation | new (API part §1) |
| `w/market.py` | every new HTTP route of §6 (marketplace, apps, schedule, catalog status/refresh, custom sources) | new; the only route owner for new routes |
| `w/upstream.py` | GitHub check/pull/inspect; job kind `upstream` dispatch | trimmed (catalog part §6, apps part §4.5) |
| `w/build.py` | build job, artifacts, retention, cleanup, `item_status`, `needs_build` | trimmed (apps part §4) |
| `w/scheduler.py` | `update` + `ota` kinds, `ensure_update()` | trimmed (apps part §5) |
| `w/projects.py` | Follow projects; gains `POST /api/projects/{id}/pull` (§6.6) | small change |
| `w/ota.py`, `w/notify.py` | label/order SQL re-pointed (apps part §3) | small change |

`server.MODULES = (catalog, upstream, projects, build, devices, ota, notify, market)`. `apps.py` and `apklocate.py` have no routes and no `register()`.

## 2. Catalog layer

Binding text: catalog part §0–§6 and its "Interfaces" section, with these amendments.

### 2.1 Validation: skip a bad bundle, reject a bad file (amends catalog part §1.3 last bullets)

- The file is rejected whole (last good copy stays, `catalog_feed.last_error` set) only when: the body is not JSON, the root is not an object, or `bundles`/`compatibilities`/`store` are missing or of the wrong type.
- Every other rule violation is local:
  - inside a bundle (its own fields, any of its patches, or a compatibility entry one of its patches points to) → that bundle is skipped;
  - inside one `store` value → that store app is skipped (the package still lists if a bundle targets it; name falls back to the package, category `NULL`).
- Each skip is recorded as `{"where": "bundles[12]" | "store.<package>", "repo": "<repo>" | null, "error": "<path>: expected <x>, got <y>"}` in the new column `catalog_feed.skipped` (JSON list, replaced on every successful parse) and logged with `log.line("[!] ...")`. `feed_status()` returns it, the Marketplace header shows the count with the messages on hover/expand.
- `parse_official(raw) -> Catalog` gains a field: `Catalog.skipped: tuple[Skip, ...]` with `@dataclass(frozen=True) class Skip: where: str; repo: str | None; error: str`. It raises `CatalogInvalid` only for the whole-file cases above.
- The same rule applies to `parse_patches_list` for built-in and custom bundles: a malformed patch entry is skipped and recorded in `bundle.list_error` (prefixed `skipped: `); a file whose root shape is wrong is rejected (old rows stay).

### 2.2 GitLab and branch (amends catalog part §4, closes its open question 1)

- GitLab bundles are stored and listed, `selectable = false`, `not_selectable_reason = "GitLab releases not supported"` (exact text, decisions).
- A catalog `branch` (today one bundle, `dev`) maps to upstream channel `prerelease` in `ensure_upstream` (decisions: accepted assumption).

### 2.3 Custom sources (closes catalog part open question 2)

A custom Morphe repo whose latest release has no `patches-list.json` is refused: 400 `"<owner>/<repo> has no patches-list.json at <tag>"`, nothing stored (catalog part §3.2 option a). Custom sources are available on web and Android (§6.5).

### 2.4 Built-in bundle on a fresh database (amends apps part §7 step 1)

Migration 010 creates the built-in upstream row itself (`INSERT OR IGNORE INTO upstream(kind, repo, channel) VALUES ('patches','MorpheApp/morphe-patches','stable')`; the unique index `(repo, channel, ifnull(tag,''))` makes it a no-op on the live DB) and then inserts the built-in bundle from that row. No Python boot step creates it. Its `store_app` rows (YouTube, YT Music) are written by the first list refresh (catalog part §2 notes).

### 2.5 Read API names and fields (unifies catalog part "Interfaces" with API part "Interfaces")

| Final name | Replaces | Returns |
|---|---|---|
| `catalog.market_apps()` | `list_apps()` in the API part | `[{package, name, category, description, origins[], bundle_count, stars, newest_version, updated_at}]`; `stars` = max `bundle.stars` among listed bundles targeting the app (new, for sort "Most stars"); `has_icon` is dropped (§2.6) |
| `catalog.app_bundles(package)` | — | `[{bundle_id, origin, host, repo, branch, name, author, description, stars, updated_at, engine, listed, selectable, not_selectable_reason, list_tag, versions[], patches[{name, description, default_enabled}]}]`; `default_enabled` is `bool | None`; unknown package → `KeyError` |
| `catalog.feed_status()` | `refresh_status()` in the API part | `{"feeds": [{feed, origin, last_ok_at, last_attempt_at, last_error, bundle_count, app_count, duplicates[], skipped[]}], "sources": [{bundle_id, origin, repo, list_tag, last_error}]}` |

`origins` lists distinct origins of the bundles targeting the package, order `builtin, custom, official`. The patch key is `default_enabled` everywhere (wire and Python), matching `bundle_info`.

### 2.6 Icons (amends catalog part §5 with icon check G1–G4 and decisions)

- `upstream.http_get(url, headers=None, timeout=30, max_bytes=None) -> (status, headers, body, final_url)`. `final_url` = `r.geturl()` (on `HTTPError`, `e.geturl()`). Every caller is updated (`upstream.fetch`, `settings.py:86`, the icon fetcher, `tests/catalog_rows.py` fake). Body over `max_bytes` → `ValueError(f"{url}: body over {max_bytes} bytes")`.
- Caps: `ICON_MAX_BYTES = 256 * 1024` for the image, `PLAY_PAGE_MAX_BYTES = 4 * 1024 * 1024` for the Play HTML page, `FEED_MAX_BYTES = 16 * 1024 * 1024` for feeds and `patches-list.json`.
- Before fetching a Play `og:image`, replace its trailing `=…` size suffix with `=s256-rw` (`PLAY_ICON_SIZE = "=s256-rw"`). Catalog `iconUrl` values are fetched as given.
- The host allowlist `ICON_HOSTS` (catalog part §5) is checked on the request URL **and** on `final_url`; `img.samsungapps.com` is not added. SVG is not accepted.
- Latency: one icon request may take up to about 20 s on a miss (Play page 10 s + image 10 s). Accepted; the server is threaded and a failure is cached (below).
- Failure handling (no silent fallback): on any failure the server logs `icon <package>: <url>: <reason>`, writes the tried URL to `store_app.icon_tried_url`, and answers 404. The route does not retry while `icon_tried_url` equals the current source URL; `POST /api/apps/{pkg}/icon/refresh` clears it. The clients show a visible letter tile on 404 (web `data-fallback`, `app.js`; Android `AppTile` initials). About 24 catalog apps end here (icon check §1).
- Every API object carries `icon_url = "/api/apps/<package>/icon"` (never null, never an external URL). `has_icon` is not used.
- Android needs no image-library change (`AppTile` already loads authenticated icons).

### 2.7 Featured and Marketplace header data

The web featured banners and the Android carousel are computed from `market_apps()` client-side: top 3 by `bundle_count`, the built-in apps, and the 3 most recently `updated_at`. `bundles.json` `featured[]` is not parsed (YAGNI). The header line ("1,207 apps · 297 community bundles · refreshed 2 h ago · built-in · N custom") comes from `feed_status()`.

### 2.8 Unchanged from the catalog part

Storage DDL (catalog part §2, plus `catalog_feed.skipped TEXT`), `FEEDS`/`FEED_RANK`, de-dup by `(host, repo, ifnull(branch,''))`, apply rules (§1.4), list refresh at the release tag (§3.3), module custom sources (§3.2), `ENGINE_CLI` (§4), deleted/reused lists (§6). `refresh(log)` raises `CatalogRefreshFailed` / `CatalogRefreshBusy` exactly as catalog part "Interfaces" says; a run where only skips happened (§2.1) is a success, not a failure.

## 3. Enabled-app model and build flow

Binding text: apps part §1–§6 and its "Interfaces", with these amendments.

### 3.1 Function names (amends apps part §4.5)

`apps.list()` is named `apps.list_enabled()` (no shadowing of the builtin). `apps.get(app_id)` returns the app object with `settings` (API part §2.2 detail); `list_enabled()` returns objects without `settings`. Neither returns `download`: `market.py` adds it from `apklocate.download_state(package)` (§5.4), so `apps.py` uses `apklocate` only for `locate` and `throttle` (update run and version check).

`apps.update_settings(app_id, fields)` handles `bundle_id`, `version`, `project_ids`, `variants`, `engine_options`, `display_name`. Download URL fields go to `apklocate.put_download(package, fields)` (§5.4). Both are called by the `PUT /api/apps/{id}` route.

### 3.2 The update run calls the locator when due (resolves apps part §5 step 4 vs. its "Rate" paragraph)

Both `{all}` and `{item_id}` runs call `apklocate.locate(package, log, cancel_event)` for every in-scope app that has no APKMirror list URL and no APKPure URL. `locate` itself decides whether a lookup is due (API part §1.4 table: `pending` always; `not_found`/`failed` after `apkm_retry_at`; `blocked` after the cooldown; `ambiguous`, `found`, `manual` never) and returns at once when it is not. An app still without a URL is not built in that run; its state shows why. The apps part "Rate" sentence "the APKMirror search … is not part of the scheduled run" is void.

### 3.3 Version check scope (interprets req §5)

req §5 "check APKMirror for a new app version" is implemented as apps part §5 step 4b: only `latest`-mode apps are checked in the run, because a new APKMirror version cannot change an `auto` or `pinned` build (apps part §4.3). The normal build still refreshes the version list at most once a day.

### 3.4 Enable from Marketplace with patch choices

`POST /api/apps` may carry `patches: {name: bool}` (API part §2.2). `apps.enable(package, bundle_id, overrides)` stores it as `patch_overrides = {name: {"enabled": bool}}` for every given name; names not in `app_bundles(package)[bundle].patches` → 400 naming the patch and the bundle. On re-enable of a disabled row with `overrides` given, the given names replace their entries and other entries stay.

### 3.5 Unchanged from the apps part

Data model (§2), item-keyed references (§3), `job.json` and shell changes (§4.1), version resolution (§4.2), fingerprint rebuild rule (§4.3), results (§4.4), enable (§4.6, with §3.4 above), update run steps 1–3 and 5–7 (§5), scheduler changes (§5), removals (§6).

## 4. Migration `010_marketplace.sql`

Binding text: apps part §7 (order, SQL patterns, content-based matching), with these amendments.

1. File `local/webui/schema/010_marketplace.sql`, one `PRAGMA foreign_keys=OFF; BEGIN; … COMMIT; PRAGMA foreign_keys=ON;` script. Contents in this order: catalog part §2 DDL (with `catalog_feed.skipped TEXT`), built-in upstream `INSERT OR IGNORE` (§2.4) then the built-in bundle (`origin='builtin', host='github', repo='MorpheApp/morphe-patches', engine='morphe', name='Morphe Patches'`), `app` columns, `item` rebuild, orphan deletes, drops, `build_jobs.inputs_sha`, schedules rebuild, jobs rebuild, orphan `upstream` + `bundle_info` deletes, seed URL rows.
2. `app.apkm_state TEXT NOT NULL DEFAULT 'pending' CHECK (apkm_state IN ('pending','found','manual','not_found','ambiguous','blocked','failed'))` (API part §1.4 wins over the nullable form in apps part §2.3). Rows with `apkmirror_list_url` or `apkpure_url` → `'manual'`.
3. Seed URL rows: the 41 `apps` entries of `w/catalog-seed.json` are written as literal `INSERT OR IGNORE INTO app(package, apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url, apkm_state) VALUES (…,'manual')` rows, generated once (scratch script) before the seed file is deleted.
4. Orphan `upstream` rows: kept = referenced by `bundle.upstream_id` or `project.upstream_id`, or one of the three `ENGINE_CLI` rows matched by content (`MorpheApp/morphe-desktop` stable, `JingMatrix/LSPatch` stable, `7723mod/NPatch` tag `v1.0.5`). Live DB: 28 rows → 8 kept (ids 1, 2, 19, 20, 23, 24, 25, 26), **20 deleted** (3–18, 21, 22, 27, 28). The requirement's "22" counted the two non-Morphe CLI rows, which `ENGINE_CLI` still needs.
5. `bundle_info`: delete rows of deleted upstreams and rows whose `tag` ≠ their upstream's `downloaded_tag`.
6. Files cannot be deleted by SQL. `build._cleanup_plan` gains `artifacts/<id>` dirs with no `item` row and `upstream/<id>` dirs with no `upstream` row (apps part §6). The existing apk-cache rule removes packages without an enabled app (live: 8 package dirs → `com.google.android.youtube`, `com.reddit.frontpage` kept).

Expected result on the live DB (verified on a `.backup` copy, plan "Live migration check"): `user_version` 10; `PRAGMA foreign_key_check` empty; `item` ids 1 (YouTube, enabled), 2 (Reddit, enabled), 3 (YT Music, disabled), all `bundle_id` = the built-in bundle, `version_mode` auto, overrides unchanged; `item_dependency` = (1,1), (3,1); 2 `device_assignments` rows kept; 41 `app` rows with URLs and `apkm_state='manual'`; `grp`, `source` gone; exactly one `schedules` row of kind `update` (old id 1, daily 09:00, enabled, `next_run_at` kept, options `{"notify": true}`) plus the OTA rows; no `jobs` row of kind `appcheck`; 8 `upstream` rows.

## 5. APKMirror auto-locate

Binding text: API part §1, with these amendments.

### 5.1 Decisions applied
- Two or more exact matches → state `ambiguous`; the user picks one candidate. No heuristic (closes API part open question 1).
- `MIN_INTERVAL = 10` s between APKMirror lookup requests, including the version check of `check-versions.sh` (closes API part open question 2).
- An app can be enabled without a URL; the update run locates (§3.2). The UI shows "Needs URL" (state `not_found`/`failed`/`blocked`) with a paste action, or "Pick one" (state `ambiguous`) with the candidates.

### 5.2 Functions (`w/apklocate.py`)

```python
MIN_INTERVAL = 10; BLOCK_COOLDOWN = 3600; NOT_FOUND_RETRY = 7 * 86400; MAX_CANDIDATES = 5; FETCH_TIMEOUT = 120
_clock = time.monotonic                  # tests replace; wall time for retry_at uses db.utcnow()
@dataclass(frozen=True)
class Cand: org: str; slug: str; title: str
def search_url(package: str) -> str
def app_url(c: Cand) -> str                       # https://www.apkmirror.com/apk/<org>/<slug>/
def list_url(c: Cand) -> str                      # https://www.apkmirror.com/uploads/?appcategory=<slug>
def parse_search(html: str) -> list[Cand]         # raises PageShape when neither div.appRow nor the no-results text
def play_id(html: str) -> str | None              # raises PageShape when the Play-link block is missing
def decide(package, name, fetch) -> Result        # pure decision table; fetch(url) -> html, raises Blocked
def throttle(cancel_event) -> None                # waits until last + MIN_INTERVAL (cancel_event.wait), records; raises Blocked during cooldown
def locate(package: str, log, cancel_event) -> None   # due check, decide, write apkm_* + URL; raises only jobs cancel
def validate_list_url(url: str) -> str            # normalised uploads URL or HttpError(400) with expected/actual
def put_download(package: str, fields: dict) -> None  # INSERT OR IGNORE app row; validates all four URL fields; list URL set -> 'manual', cleared -> 'pending'
def download_state(package: str) -> dict | None   # the API part §2.2 "download" object; None when no app row
def reset_lookup(package: str) -> None            # manual retry: 409 apkmirror_blocked inside cooldown (retry_at), 409 when found|manual; else state 'pending', candidates cleared
```

`Result(state, list_url, candidates, error)`; `Blocked(Exception)`, `PageShape(Exception)`. `locate` reads the ranking name from `store_app.name` (falls back to the package). Fetch = `src/build/fetch-page.sh <url> <out>` run with `jobs.run_isolated(..., env_keys=build.SOLVER_ENV, timeout=FETCH_TIMEOUT)` (API part §1.3).

### 5.3 Manual retry

`POST /api/apps/{id}/locate` → `apklocate.reset_lookup(package)` then `apps.check_update(id)` (the `{item_id}` run, where `locate` finds `pending` due). Replaces the "ignores retry_at" wording of API part §1.4.

### 5.4 Download object

`download_state(package)` returns `{state, apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url, candidates[{title, url, list_url}], error, retry_at, checked_at}`. `apps.get`/`list_enabled` derive `status.state = "needs_download_url"` themselves from the URL columns (no import of `download_state`).

## 6. HTTP API

Binding text: API part §2–§4, with these amendments. Every route below accepts a session cookie or a device token; none is added to `auth.SESSION_ONLY`. All new handlers live in `w/market.py`.

### 6.1 Marketplace
- `GET /api/marketplace`: `sort` ∈ `name` (default) | `bundles` | `stars` | `updated`; `origin` ∈ `official|builtin|custom`; `enabled` ∈ `true|false`; `page`, `per_page` 1–100 (default 50); unknown values → 400 with expected/actual. App entries: `{package, name, category, icon_url, bundle_count, stars, origins[], updated_at, app_id, enabled}`. Response adds `"feed": feed_status()` so the header needs no second call.
- `GET /api/marketplace/{pkg}`: as API part §2.1, `bundles` = `catalog.app_bundles(pkg)` (§2.5 fields), `download` = `apklocate.download_state(pkg)` (may be null).

### 6.2 Apps
- `POST /api/apps` body `{package, bundle_id, patches?: {name: bool}, download?: {apkmirror_list_url?, apkpure_url?}}`. Order: validate body → `apklocate.put_download` when `download` given → `apps.enable(package, bundle_id, overrides)` (enqueues the `{item_id}` run). 201 app object (with `download`). A disabled row is re-enabled (201); an enabled row → 409 `already_enabled`. No URL is required (decisions).
- `PUT /api/apps/{id}`: `download` part → `apklocate.put_download`; other parts → `apps.update_settings`. When a list URL or APKPure URL went from empty to set, the route calls `apps.check_update(id)`. Returns the app object.
- `DELETE /api/apps/{id}` → `apps.disable`; `POST /api/apps/{id}/check` → `apps.check_update`; `POST /api/apps/{id}/build` → `apps.rebuild` (409 `needs_download_url`, 409 `app_disabled`); `POST /api/apps/{id}/locate` (§5.3); `GET|PUT /api/apps/{id}/patches` → `apps.get_patches` / `apps.put_patches`; `GET /api/apps/{id}/artifacts` → `build.list_artifacts(item_id)`; `GET /api/apps` → `apps.list_enabled()` + `download`.
- Icon routes `GET /api/apps/{pkg}/icon`, `POST /api/apps/{pkg}/icon/refresh` stay in `catalog.py` (path unchanged).

### 6.3 Schedule
- `GET /api/schedule` → `apps.get_schedule()`; `PUT /api/schedule` → `apps.put_schedule(fields)` (`spec` checked by `scheduler._check_spec`; forms day/week/hours, no cron); `POST /api/schedule/run` → 202 `{"job_id"}` = `jobs.enqueue('upstream', {'all': True}, 'manual', schedule_id, {'notify': <row notify>})`. The web Apps "Check all for updates" button calls this route.
- `/api/schedules*` (scheduler.py) serve kind `ota` only; kind `update` rows are hidden there; POST/PUT with kind ≠ `ota` → 400.

### 6.4 Catalog status and refresh
`GET /api/catalog/status` → `catalog.feed_status()`; `POST /api/catalog/refresh` → synchronous `catalog.refresh(log)` with a `jobs`-free log adapter (lines go to the server log); 200 status; 409 `catalog_refresh_busy`; 502 with the failure text (applied parts stay).

### 6.5 Custom sources (web and Android)
`GET /api/sources` → `{"sources": catalog.list_custom()}`; `POST /api/sources` → 201 `{"bundle_id"}`; `DELETE /api/sources/{id}` → 204, 409 `source_in_use` with `item_ids`. Available to device tokens (Android mockup has "add custom source"); API part's "W only" label is void.

### 6.6 Follow-project pull (closes API part §3 note on `…/pull`)
`POST /api/projects/{id}/pull` (in `projects.py`) → enqueues `upstream` `{upstream_id: <project's upstream>}` → `{"job_id"}`. Used by `projects.js` and by the Builds page retry of project pulls. `POST /api/upstreams/{id}/pull`, `GET /api/upstreams`, `POST /api/upstreams/check` are deleted.

### 6.7 Error envelope
API part §4, plus codes `app_disabled` (rebuild/check of a disabled app), `patch_unknown` (enable with an unknown patch name, 400).

## 7. Web UI (Fluent 2, light only)

Reference: `web/*.html`, `web/fluent.css`, root `icons.js`, root `tokens.md` §Web at 0d55fc3, used as is, except the Enable rule (§7.3) and the schedule "Custom" form (§7.5).

### 7.1 Foundation
- `static/app.css` = the token block and component classes of `web/fluent.css` (same class names), plus the existing utility classes still used by kept pages; light only. Delete every dark-mode rule (`html[data-theme=dark]`), the theme toggle (`data-theme-toggle`, `app.js:130-135`), the inline theme bootstrap `<script>` in every page head, the dark `theme-color` meta (one `<meta name="theme-color" content="#ffffff">` stays), and the Settings "Appearance" card.
- `static/app.js`: the root `icons.js` sprite becomes an inline `<symbol>` block injected once by `shell()`, with `icon(name)` returning `html\`<svg class="i" aria-hidden="true"><use href="#i-${name}"/></svg>\``. Every icon-only button has `aria-label` and `title`. Nav: Marketplace `/`, Apps `/apps.html` (count badge), Schedules `/schedules.html`; group "Manage": Builds, Devices, Followed projects, Settings. `catalogItems` is deleted; consumers use `GET /api/apps`. `STATUS`/`itemStatus` map the §6.2 status states; `iconTile` keeps its letter-tile fallback.
- No external URLs, html-template-only, every `$('#id')` exists in its page, one manifest link, `<aside class="side" aria-label="Primary">` + `#toast` (existing `test_ui_static.py` rules).

### 7.2 Pages
| Page | Files | Replaces |
|---|---|---|
| Marketplace (`/`) | `index.html`, `index.js` | old App list |
| Apps | `apps.html`, `apps.js` (list + settings drawer) | old App list rows + `item.html`/`item.js` (deleted) |
| Schedules | `schedules.html`, `schedules.js` | multi-target editor |
| Builds, Devices, Followed projects, Settings, Login | existing files | restyle + re-pointed calls |

### 7.3 Marketplace
Header with feed status (§2.7) and "Refresh catalog"; featured banners; category pills with counts; search; origin filter; sort (Most bundles, Most stars, Recently updated, Name A–Z); card gallery (category gradient tile or icon); expand-in-place panel: (1) bundle radio cards with origin label, stars, patch count, updated, disabled with `not_selectable_reason` when not selectable; (2) patches of the chosen bundle with switches (default from `default_enabled`; `null` → "default unknown"; changed ones marked "changed by you"); (3) download source: current `download.state` text when known, optional APKMirror listing URL and APKPure URL fields. **"Enable and build" is enabled as soon as a selectable bundle is chosen** (decisions override the mockup's disabled state); the URL fields are optional. Enabled apps show "Enabled" and link to Apps. "Add custom source" dialog (URL, engine Morphe/LSPatch/NPatch, asset regex + target packages for Xposed engines) and a custom-source list with delete.

### 7.4 Apps
Columns App (icon, name, package), App version (+ version mode, variant), Patch bundle (name, origin label, tag), Status (badge + one line: progress %, fail reason + "View log" link to `/builds.html?job=<id>`, "Add URL" / "Pick one" for download states), Actions (icon buttons: check for update, rebuild, download menu of the latest artifacts, settings). Filter pills All / Update available / Building / Failed / Needs URL, search, "Check all for updates" (§6.3), "Add from Marketplace", next scheduled run. Settings drawer: Enabled switch (off = `DELETE /api/apps/{id}`), bundle choice (from `GET /api/marketplace/{pkg}`; switching keeps same-name overrides, stale ones listed), patches + options (port `item.js` patch list, `fieldFor`, `overridesFrom`), version Auto/Pinned/Latest + pin + allow nearest, ABI variants, Xposed module options (`engine_options`) for module bundles, Follow-project dependencies, download URLs (state, candidates to pick, paste), display name, refresh icon; Save / Save and rebuild / Cancel. Refresh every 5 s while visible with the existing `every()` helper.

### 7.5 Schedules
Hero card: presets 1 h, 3 h, 6 h, 12 h, 24 h (`{every:'hours', hours:n}`), "Custom" = the existing day/week/hours forms (no cron expression: the scheduler has none), enabled switch, notify switch, next run, Run now; the five-step run pipeline with the last run time and result; OTA schedule list and its dialog (kind `ota` only).

### 7.6 Kept pages
Builds: label = app name · bundle name (no `source_name`); retry: build jobs → `POST /api/apps/{id}/build`; upstream jobs → by target (`{item_id}` → `/check`, `{all}` → `/api/schedule/run`, `{upstream_id}` of a project → `/api/projects/{id}/pull`). Devices: assign dialog lists `GET /api/apps`. Followed projects: pull via §6.6; drop the `/api/schedules` load if it only fed app schedules. Settings: drop "Appearance" and "CLI update" (`#gh-cli`); cleanup text says "apps that are not enabled". Login: restyle only.

## 8. Android UI (Material 3, one fixed light palette)

Reference: `android/*.html`, `android/tokens.md` (final palette by role name), `android/icons-material.js`.

### 8.1 Theme and components
- `RelumeLight = lightColorScheme(...)` with `android/tokens.md` §1–§2 values; extended pastel set (§3) as `@Immutable data class RelumeColors` via `staticCompositionLocalOf`; `Typography` one step down (§4); MD3 default shapes (§5). No `dynamicLightColorScheme`, no dark scheme, no `isSystemInDarkTheme`. `res/values/themes.xml` parent becomes a light theme (`android:Theme.Material.Light.NoActionBar`).
- `ui/Components.kt` is rewritten on `androidx.compose.material3` (already in the Compose BOM; no new dependency): `AppTile` keeps its authenticated icon loading and initials fallback; status pill, origin label, category tag, list item, inset list group helpers per `android/tokens.md` §3/§5.
- Icons: official Material Symbols Rounded SVGs (`android/tokens.md` §6 names) as vector drawables `res/drawable/ms_<name>.xml` (path data unchanged, viewport `0 -960 960 960` handled with a `translateY="960"` group), used with `painterResource`. `object Icons` and `Ic()` are deleted. No icon library.

### 8.2 Navigation
`NavigationBar` with Marketplace, Apps, Schedules (indicator colours amber, blue, teal). The account avatar in the top app bar opens Settings (existing `SettingsScreen` sections). Screens: Marketplace, Marketplace app detail, Download-source step, Apps, App settings, Schedules, Settings, Log (single job log, opened from a Building/Failed card), Install (install-run progress). Deleted: Builds tab and Builds list (web-only build history), Detail screen.

### 8.3 Behaviour (same as web unless noted)
- Marketplace: search bar, category chips, featured carousel (§2.7), two-line app list; app detail with bundle radio list, patch switches, then Download source step with optional URLs and "Enable and build" (enabled when a selectable bundle is chosen); add-custom-source dialog from the top bar.
- Apps: filter chips All / Not installed / Phone update / Failed / Needs URL; card with tile, name, version · bundle, status pill, check and rebuild icon buttons, progress; bottom row for this phone: Not installed → Install, older installed version → Update, Installing, Installed + version. **No Download button.** Card tap → App settings (same fields as web §7.4) with Save / Save & rebuild.
- Install / Update: if the app is not assigned to this phone, `PUT /api/devices/{id}/assignments` with the current assignments plus this app; then `GET /api/me/plan?items=<id>` and `InstallRunner.start(context, plan, Origin.UI)`. The planner installs the app's Follow-project dependencies first (req §3). The system package installer shows its own dialog.
- Schedules: interval card with chips 1 h/3 h/6 h/12 h/24 h/Custom (bottom sheet with the day/week/hours forms), next run, Run now, last-run pipeline; OTA schedules with switches; FAB "OTA schedule" → bottom sheet (name, repeat, at, retries, phones from `GET /api/devices`).
- API client (`api/RelumeApi.kt`, `api/Models.kt`): DTOs and calls for §6.1–§6.5; delete `Catalog`, `Group`, `catalog()`, `item()`, `setSwitches`, `update(id, rebuild)`, `checkAll`, every `/api/items/*` path.

## 9. Removals (no backward compatibility)

Union of: catalog part §6 "Deleted", apps part §6 tables (code, seed, version in shell, orphan data, tests), API part §3 routes, `docs/logs/2026-10-26-explore-frontend.md` §5 (web and Android, groups / source × item / switches / multi-target schedules), §6.6 upstream routes, §7.1 dark mode, §8.1–§8.2 Android Fluent theme, Builds tab, Detail screen. Docs: `README.md` catalog/seed/export-seed sentences rewritten for Marketplace; `docs/Frequently Asked Questions.md:23-25` removed. Mechanical end check (plan contract): the grep list in the plan returns no hit.

## 10. Tests

Policy (project `CLAUDE.md`): commit only core logic, data integrity, security, past bugs; ≤ 1 s each; hermetic; full suite ≤ 90 s.

Committed (new or rewritten):
| File | Content (source) |
|---|---|
| `w/tests/test_catalog_feed.py` | catalog part §7 tests 1–7, with test 2 split: whole-file rejection cases vs. per-bundle skip recorded in `catalog_feed.skipped` (§2.1); module custom-source add rules |
| `w/tests/test_apps.py` | apps part §8 tests 2–5, patches/version validation ported from `test_catalog_store.py`, module variant/engine-option rules from `test_catalog_xposed.py`, enable with patch choices (§3.4) |
| `w/tests/test_core_db.py` | apps part §8 test 1 (migration 010 fixture DB built by 001–009), replaces `test_old_schedule_rows_become_app_rows` |
| `w/tests/test_core_scheduler.py` | apps part §8 test 6; app-kind tests deleted |
| `w/tests/test_apklocate.py` | API part §5 rows 1–5 (parser, exact match, decision table, limiter with fake clock, URL validation) |
| `w/tests/test_market_api.py` | API part §5 rows 6–8 (filter/sort/pagination, enable 201/409, device token reaches the routes, `SESSION_ONLY` still 403) |
| `w/tests/test_build_service.py` | `needs_build` matrix (apps part §8 test 3), cleanup plan orphan dirs; appcheck tests deleted |
| `w/tests/test_catalog_upstream.py` | check/pull/download/prune/inspect kept; `bundle_info` prune; `http_get` `final_url` + `max_bytes`; source/item fan-out tests deleted |
| `src/build/test-build-item.sh` | `.version = {value, near}`; `runner_version_unresolved` case deleted |
| `android/.../api/MarketParseTest.kt` | DTO parsing of §6.1/§6.2 sample JSON (contract with the server, data integrity) |

Deleted: `test_catalog_seed.py`, `test_build_zip.py`, `test_catalog_store.py`, `test_catalog_xposed.py`; in `test_ui_static.py`: `test_index_source_controls`, `test_item_page_has_no_mockup_leftovers`, `test_item_js_keeps_colour_picker_and_version_labels` (pages gone); `test_core_startup.py` seed step expectations.
Not committed: live-copy migration run, live `bundles.json` parse, live APKMirror requests, UI rendering, route-forwarding tests.

## 11. Deployment

1. After the round-2 sign-off, the user runs `scripts/release.py <version>` and redeploys the stack in Portainer (the lead does not).
2. Before the redeploy, the user backs up the deployed database (`sqlite3 <data>/webui.db ".backup <data>/webui.db.bak-pre-010"`).
3. Migration 010 runs at the first boot of the new image (`db.migrate`). The first update run rebuilds YouTube and Reddit once (`inputs_sha` NULL, apps part §4.3).
4. The user presses Settings → Clean up once to remove orphan `upstream/<id>`, `artifacts/<id>` and `apk-cache` dirs.
5. Android: a new companion release by tag `companion-vX.Y.Z` (user), because the old app calls deleted routes.

## 12. Resolved conflicts

1. **Bad bundle in `bundles.json`**: catalog part §1.3 rejects the whole file; decisions skip the bundle and show the error. → §2.1 (skip per bundle/store entry; whole-file rejection only for non-JSON or wrong root shape). Amends catalog part §1.3, §7 test 2.
2. **GitLab bundles**: catalog part open question → listed, not selectable, reason text "GitLab releases not supported" (decisions; catalog part said "GitLab not supported"). §2.2.
3. **Custom repo without `patches-list.json`**: catalog part open question → refused with a message. §2.3.
4. **Catalog `branch: dev`**: catalog part uncertainty → prerelease channel (decisions). §2.2.
5. **Ambiguous APKMirror match**: API part open question → user picks, no heuristic. §5.1.
6. **APKMirror request spacing**: API part open question → 10 s. §5.1.
7. **Enable without a download URL**: mockup README/report, `web/marketplace.html` and `android/marketplace.html` disable Enable until a URL is typed → decisions allow enabling at once; Apps shows "Needs URL"/"Pick one". §7.3, §8.3.
8. **Locate in the scheduled run**: apps part §5 "Rate" says the search is not part of the scheduled run; apps part §5 step 4 and API part §1.4/§1.5 say it runs when due → it runs in every update run when due, and apps without a URL are not built. §3.2.
9. **Manual locate vs. "never retried" ambiguous state**: API part §1.4 says the manual route ignores `retry_at`, but the run's due check never retries `ambiguous` → the route resets the state to `pending` first. §5.3.
10. **Interface names**: `market_apps` vs `list_apps`, `feed_status` vs `refresh_status`, `default` vs `default_enabled`, `unselectable_reason` vs `not_selectable_reason`, `apps.list` → `market_apps`, `feed_status`, `default_enabled`, `not_selectable_reason`, `list_enabled`. §2.5, §3.1.
11. **`has_icon` / null `icon_url`**: API part sends `icon_url: null` without `has_icon`, while catalog part fetches icons lazily (so "has an icon" is unknown before the first fetch) → `icon_url` is always the icon route; a failure is a recorded 404 and the client letter tile. §2.6.
12. **Icon fetch details**: catalog part §5 single 256 KiB cap, no final-URL check possible with today's `http_get` → icon check G1–G4 and decisions (two caps, `=s256-rw`, `final_url`, ~20 s latency). §2.6.
13. **`apkm_state` nullability**: apps part §2.3 nullable vs API part §1.4 `NOT NULL DEFAULT 'pending'` → API part. §4 item 2.
14. **Built-in bundle on a fresh DB**: apps part §7 says part 1 creates it at boot; catalog part §3.1 says the migration inserts it → migration only, with `INSERT OR IGNORE` of the upstream row. §2.4.
15. **Orphan upstream count**: req §7 says 22; parts keep the LSPatch/NPatch CLI rows for `ENGINE_CLI` → 20 deleted on the live DB. §4 item 4.
16. **Android dynamic colour**: req §6 and root `tokens.md` (dynamic colour with a fallback for API < 31) vs decisions (no dynamic colour, one fixed palette) → decisions; `android/tokens.md` is the palette. §8.1.
17. **Android icons**: root `tokens.md` ports paths into `object Icons`; `android/tokens.md` and decisions require official Material Symbols → vector drawables from the official SVGs, `object Icons` deleted. §8.1.
18. **Android Download vs. Install**: req §6 "same behaviour as web" (web has Download) vs decisions (Android Install, no Download) → decisions. §8.3.
19. **Custom sources on Android**: API part labels the source routes "W only"; the Android mockup has "add custom source" and req §6 asks for the same Marketplace behaviour → routes open to device tokens, Android has the dialog. §6.5.
20. **Schedule "Custom" with cron**: `web/schedules.html` shows a cron field; the scheduler has no cron (`scheduler._check_spec`: day/week/hours) and req §5 asks for one interval → "Custom" uses the existing three forms; no cron parser is added. §7.5.
21. **Follow-project pull route**: API part §3 deletes `POST /api/upstreams/{id}/pull` but `projects.js` and the Builds retry use it → new `POST /api/projects/{id}/pull`. §6.6.
22. **Sort "Most stars"**: `web/marketplace.html` offers it; API part sort list lacks it → `sort=stars`, `market_apps()` gains `stars`. §2.5, §6.1.
23. **Featured banners**: mockup shows them; no part defines their data → computed client-side from `market_apps()`. §2.7.
24. **`upstream.http_get` return shape**: catalog part adds only `max_bytes`; decisions require the final URL → 4-tuple with `final_url`, every caller updated. §2.6.
25. **Install brings dependencies**: decisions describe Install as "pulls the server-built APK"; req §3 says install brings Follow dependencies along, and the existing plan endpoint only covers assigned apps → Install assigns the app to this phone and runs the existing plan. §8.3.

## 13. Open questions for the user

None.

## 14. Known limitations (accepted)

- Catalog part §9 uncertainties: catalog `default` missing for 1361 patches (shown as "default unknown" until enabled); `branch: dev` mapping is an assumption.
- APKMirror block cooldown is in memory; a restart resets it (API part §1.4).
- `allow_nearest` fingerprint ceiling (apps part §4.3).
- Play icon markup or rate limits can change; failures show the letter tile and are logged (icon check uncertainties).
- After the migration the Threads fork upstream rows (27, 28: `com.instagram.barcelona`) are deleted as orphans; the user can re-add the fork as a custom source.
