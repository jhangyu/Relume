# Marketplace backend — part 1: catalog layer

Status: design draft by be-catalog, 2026-10-26. Input: `docs/superpowers/specs/2026-10-26-marketplace-requirements.md` (binding). Code refs at HEAD `991710b`; `w/` = `local/webui/`.

Scope: fetch + validate catalog feeds, normalized catalog storage, built-in and custom sources (Morphe and Xposed), the authoritative per-app patch list for enabled apps, icons, deletions, tests. Not in scope: the enabled-app table, build flow, schedule (be-apps); APKMirror lookup and HTTP shapes (be-api).

## 0. Facts measured on the 2026-10-26 sample (`.claude/tmp/morphe-site/bundles.json`, 2.83 MB)

Scratch scripts: `.claude/tmp/be-catalog/shape*.py`, `s4.py`, `pl.py` (run with `python3 -I`).

- Top keys `bundles[297]`, `compatibilities[1517]`, `store{1207}`, `featured[]`. 290 github + 7 gitlab bundles; 1 bundle has `branch: "dev"`.
- **Patch records come in two dialects:** `{default, compatiblePackagesKey}` (4647) and `{use, compatibilityKey}` (726). 716 patches have neither key = universal patches. 1361 patches have neither `default` nor `use`.
- **Compatibility entries come in three dialects:** list of `{packageName, name, targets[{version?, versionCodes?, ...}]}` (1460); dict `{package: [versions]}` (57, one empty `{}`); one list entry with `versions: [..]` instead of `targets`. 200 targets have no `version` (`targets: [{}]`), one entry has `targets: null`, one list is empty.
- One repo is listed twice (`epabafree/sabp-eppy-patches`, differs only in `hotRank`).
- `store` covers every `targetApps` package. **YouTube and YT Music are not in `store`** (Reddit is). The built-in `patches-list.json` (MorpheApp/morphe-patches 1.48.0) targets exactly these 3 packages and carries full options.
- iconUrl hosts: play-lh.googleusercontent.com 1172, none 22, raw.githubusercontent.com 5, lh3.googleusercontent.com 3, lh3.ggpht.com 2, 3 single-use hosts.
- 3 bundles have only universal patches.
- A scratch prototype of the §1.3 grammar (`.claude/tmp/be-catalog/proto.py`) accepts the sample: 296 bundles after de-dup, 1205 packages with compatibility data, 1522 bundle→app pairs. (`targetApps` lists 1207; 2 packages have no compatibility entry and get no `bundle_app` row.)

## 1. Fetch and validation

### 1.1 Feeds

One code constant, the seam the requirements ask for (§2: a second catalog = new parser + de-dup by repo):

```python
FEEDS = (("official", "https://morphe-patches.software/data/bundles.json", parse_official),)
FEED_RANK = {"builtin": 0, "custom": 1, "official": 2}   # lower wins on de-dup; awesome would be 3
```

No class hierarchy: a parser is a plain function `bytes -> Catalog`. ponytail: one tuple, add a row for awesome-morphe.

### 1.2 HTTP

`catalog.refresh(log)` per feed:
- `upstream.http_get(url, headers, timeout=30)` (`w/upstream.py:49`), headers `User-Agent: Relume`, `If-None-Match: <stored etag>`. No GitHub token (public data).
- Size cap: the response body is read with a hard limit `FEED_MAX_BYTES = 16 MiB` (5.7x today's size). `http_get` reads the whole body today; add an optional `max_bytes` argument that reads `max_bytes + 1` and raises `ValueError(f"{url}: body over {max_bytes} bytes")`. One generic change, also used by the icon fetch (§5).
- 304 → only `last_attempt_at` updates. 200 → parse (§1.3). Any other status, network error, oversize or parse failure → `catalog_feed.last_error = "<what>: expected <x>, got <y>"`, last good copy stays in use, `log.line("[!] ...")`. The error is shown in the Marketplace header (be-api exposes `feed_status()`) and in the global-run notification.
- Last good copy: the validated raw bytes in `DATA/catalog/<feed>.json` (written to `.part`, `os.replace`). `catalog` joins `db.SUBDIRS` (`w/db.py:9`). The runner never needs it. The ETag is stored only together with a successful write, so a failed parse never pins a bad ETag.
- Concurrency: one module lock `_refresh_lock` (owner: catalog module); a second caller gets `HttpError(409, "catalog refresh already running")` on the route and is skipped by the global run.

### 1.3 Parse, don't validate

`parse_official(raw: bytes) -> Catalog` converts the JSON into frozen dataclasses in one pass; nothing past this function reads the raw dict.

```python
@dataclass(frozen=True)
class Patch:      name: str; description: str; default: bool | None; packages: tuple[str, ...]   # () = universal
@dataclass(frozen=True)
class Bundle:     host: str; repo: str; branch: str | None; name: str; author: str; description: str | None
                  stars: int; updated_at: str | None; apps: dict[str, tuple[str, ...]]; patches: tuple[Patch, ...]
@dataclass(frozen=True)
class StoreApp:   package: str; name: str; category: str; description: str | None; icon_url: str | None
@dataclass(frozen=True)
class Catalog:    bundles: tuple[Bundle, ...]; store: dict[str, StoreApp]; duplicates: tuple[str, ...]
```

Accepted grammar (anything else raises `CatalogInvalid(f"{path}: expected {x}, got {y!r}")`, e.g. `bundles[12].patches[3].compatiblePackagesKey: expected index < 1517, got 2001`):
- root: object with list `bundles`, list `compatibilities`, object `store`. Unknown keys ignored everywhere (the site adds ranking fields freely; ignoring them is not a default for missing data).
- bundle: `source` ∈ {github, gitlab}; `repo` matches `server.REPO` (`owner/name`, GitLab allows nested groups → `[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+`); `branch` absent or slug; `name`, `author` non-empty str; `patches` list; `stars` int; `updatedAt` int ms or null → ISO via `db.iso`.
- patch: `name` non-empty str; `description` str or absent (→ `""` is NOT used: absent → `None`, UI prints nothing); `default` or `use` bool or absent → `None` (unknown, the catalog is for browsing only; the real default comes from the downloaded bundle §4); compat index = `compatiblePackagesKey` or `compatibilityKey` (never both), int in range, or absent/null = universal.
- compatibility entry: list of `{packageName, targets: list|null}` or `{packageName, versions: [str]}`, or dict `{package: [str]}`. A target without `version` contributes no version. Versions per package = union over the bundle's patches, sorted with `catalog.version_key` (`w/catalog.py:23`) newest first; `()` = the bundle lists no versions (any version, UI says "any").
- package names match `server.PACKAGE` (`w/server.py:84`).
- store value: `name`, `category` non-empty str; `iconUrl` absent or `https://` str; `description` str or absent (HTML entities unescaped with `html.unescape`).
- Duplicate `(host, repo, branch)` in one feed: first record wins, the repo goes into `Catalog.duplicates`, which `feed_status()` shows (today: 1). Not an error, because today's file has one; not silent, because it is listed.
- A file that fails any rule is rejected whole (requirements §2). Known limitation: one malformed third-party bundle freezes catalog updates until the site fixes it; the UI shows why. Upgrade path if that bites: reject per bundle and list rejected repos in `feed_status()`.

### 1.4 Apply

After the feeds are fetched, `refresh` re-parses every feed's last good copy from disk (measured 0.03 s for the 2.8 MB sample with the scratch prototype) and merges by `(host, repo, ifnull(branch,''))` in `FEED_RANK` order (first wins), then applies in one `db.tx()`:
- upsert `bundle` rows of catalog origin (id stays stable), replace their `bundle_app` / `bundle_patch` rows;
- a catalog-origin bundle that left every feed: deleted when no enabled app chose it, else kept with `listed = 0` (UI: "removed from catalog", builds continue from the repo);
- a repo that is also a built-in or custom bundle is skipped (better rank owns the row);
- upsert `store_app` metadata from the merged store (catalog feeds only); icon cache columns are not touched unless `icon_url` changed.
Apply runs only when at least one feed returned 200 this run, or the DB has no catalog rows yet.

## 2. Storage (new file `schema/010_*.sql`, merged by be-apps into the one migration)

```sql
CREATE TABLE bundle (
  id INTEGER PRIMARY KEY,
  origin TEXT NOT NULL CHECK (origin IN ('official','builtin','custom')),
  host TEXT NOT NULL CHECK (host IN ('github','gitlab')),
  repo TEXT NOT NULL,
  branch TEXT,
  engine TEXT NOT NULL DEFAULT 'morphe' CHECK (engine IN ('morphe','lspatch','npatch')),
  name TEXT NOT NULL, author TEXT, description TEXT, stars INTEGER, updated_at TEXT,
  asset_regex TEXT,                -- custom Xposed modules only; NULL = upstream.DEFAULT_REGEX
  listed INTEGER NOT NULL DEFAULT 1,
  upstream_id INTEGER REFERENCES upstream(id),   -- release tracker; created on first enable (catalog) or on add (builtin/custom)
  list_tag TEXT,                   -- builtin/custom: release tag whose patches-list.json fills bundle_patch
  list_error TEXT,                 -- builtin/custom: last list refresh failure
  CHECK (engine = 'morphe' OR (origin = 'custom' AND asset_regex IS NOT NULL)));   -- modules: custom only, regex required
CREATE UNIQUE INDEX bundle_key ON bundle(host, repo, ifnull(branch, ''));

CREATE TABLE bundle_app (          -- "bundle B targets package P"; drives the Marketplace
  bundle_id INTEGER NOT NULL REFERENCES bundle(id) ON DELETE CASCADE,
  package TEXT NOT NULL,
  versions TEXT NOT NULL,          -- JSON array newest first; [] = no version list
  PRIMARY KEY (bundle_id, package));
CREATE INDEX bundle_app_pkg ON bundle_app(package);

CREATE TABLE bundle_patch (        -- browsing list; the build uses bundle_info (§4)
  bundle_id INTEGER NOT NULL REFERENCES bundle(id) ON DELETE CASCADE,
  package TEXT NOT NULL,           -- '*' = universal (constant UNIVERSAL)
  name TEXT NOT NULL,
  description TEXT,
  default_enabled INTEGER,         -- NULL = the source did not say
  PRIMARY KEY (bundle_id, package, name));

CREATE TABLE store_app (           -- per-package metadata, one row per package any source targets
  package TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT,                   -- NULL = not in a catalog store (built-in/custom only)
  description TEXT,
  icon_url TEXT,
  icon_file TEXT, icon_tried_url TEXT);   -- icon cache (§5)

CREATE TABLE catalog_feed (
  feed TEXT PRIMARY KEY, etag TEXT, last_attempt_at TEXT, last_ok_at TEXT, last_error TEXT, duplicates TEXT);  -- duplicates JSON
```

Notes:
- Universal patches never create `bundle_app` rows (otherwise 3 bundles would appear under all 1207 apps); they are shown inside bundles that target the app.
- `store_app` names for packages outside any catalog store (YouTube, YT Music) come from `patches-list.json` `compatiblePackages[].name`, written only when no catalog row exists.
- `app` (be-api/be-apps: download URLs and lookup status) stays a separate table; `app.icon_file`, `app.icon_fetched_at` move to `store_app` and are dropped.
- `bundle_info(upstream_id, tag, package, patches)` (`schema/003:56-61`) is kept unchanged as the authoritative patch list; add pruning of rows whose tag is no longer `downloaded_tag` in `upstream._prune` (`w/upstream.py:372`, today never pruned).
- Second catalog: `FEEDS` row + `parse_awesome` + `'awesome'` in the `origin` CHECK and `FEED_RANK`. No table, column or UI change beyond the CHECK literal. ponytail: the CHECK literal is the only schema touch; drop the CHECK if that is judged a "schema change".

## 3. Built-in and custom sources

All three origins end in the same rows (`bundle` + `bundle_app` + `bundle_patch` + `store_app`), so Marketplace reads one view.

### 3.1 Built-in

Code constant `BUILTIN = ("github", "MorpheApp/morphe-patches")`, row inserted by migration with `origin='builtin'`, `engine='morphe'` and an `upstream` row (kind `patches`, channel `stable`). The migration reuses the existing upstream row id 2 (`MorpheApp/morphe-patches` stable) so downloaded files and `bundle_info` survive.

### 3.2 Custom

`add_custom(url, engine, asset_regex=None, packages=())`:
- `url` → `server.validate_repo` (`w/server.py:90`, accepts `https://github.com/o/r`). GitHub only.
- `engine='morphe'`: creates `upstream` (kind `patches`, stable) + `bundle(origin='custom')`, then runs the list refresh (§3.3) synchronously; a repo without `patches-list.json` at its latest release tag is rejected with 400 `"<repo> has no patches-list.json at <tag>"` (nothing stored). Upgrade path: list unfiltered with the CLI after download (`parse_list_patches` already handles many packages).
- `engine='lspatch'|'npatch'` (Xposed module): `asset_regex` required (same rule as `xposed_source_rules`, `w/catalog.py:305`), `packages` = 1..20 target packages given by the user (a module APK does not declare a target app the server can read without running it). Creates `upstream` (kind `module`) + `bundle` + one `bundle_app(versions='[]')` per package; no `bundle_patch` rows. Version mode `auto` stays unavailable for module apps (rule `w/catalog.py:319`, moves to be-apps).
- A repo already present as a catalog bundle: the row is converted in place (`origin='custom'`, same id) so an enabled app keeps its bundle. A repo already custom/builtin: 409.
- `delete_custom(bundle_id)`: 409 when an enabled app uses it; else deletes bundle (cascade) and its upstream row + `DATA/upstream/<id>/`. If the repo is in a catalog feed it reappears as official on the next apply.

### 3.3 List refresh (built-in + custom Morphe)

Inside `refresh`, per bundle with `origin IN ('builtin','custom') AND engine='morphe'`:
1. `upstream.check(upstream_id)` (`w/upstream.py:170`) → `latest_tag` (token used if set, as today).
2. If `latest_tag != list_tag`: GET `https://raw.githubusercontent.com/<repo>/<latest_tag>/patches-list.json` (cap 16 MiB), `parse_patches_list(raw) -> (version, patches, apps, names)` with the same strictness as §1.3 (`patches[].{name, description, default bool, compatiblePackages: list|null}`; null = universal). Replace the bundle's `bundle_app`/`bundle_patch`, set `list_tag`, clear `list_error`.
3. Failure → `list_error`, old rows stay. Fetching at the tag (not `main`) keeps the browse list in step with the release that will be downloaded.
Module bundles only get step 1 (release visible in the UI).

## 4. Download and the exact patch list (enabled apps only)

Unchanged mechanism, re-keyed from source to bundle:
- Enabling an app on a catalog bundle calls `ensure_upstream(bundle_id)`: `catalog.upstream_row(c, kind, repo, channel, None, asset_regex)` (`w/catalog.py:328`, kept) with kind `patches` (morphe) or `module`, channel `stable` (`prerelease` when the catalog gives a `branch`; uncertain, see §9). Stores `bundle.upstream_id`.
- GitLab bundles: `selectable = False` (download code is GitHub-only, `w/upstream.py:15`); shown with "GitLab not supported". 7 of 297.
- CLI per engine is a code constant, replacing `source.cli_upstream_id`:
  `ENGINE_CLI = {"morphe": ("MorpheApp/morphe-desktop", "stable", None), "lspatch": ("JingMatrix/LSPatch", "stable", None), "npatch": ("7723mod/NPatch", "tag", "v1.0.5")}` with the regexes from today's seed rows 1/19/20 (`^morphe-desktop-.*-all\.jar$`, `^lspatch-.*-release\.jar$`, `^jar-.*-release\.jar$`). `engine_cli(engine)` returns the upstream id via `upstream_row` (migration keeps rows 1/19/20).
- `upstream.pull` (`w/upstream.py:423`) and `inspect_row` (`:286`) stay; their source joins change:
  - `inspect_row` CLI = `engine_cli('morphe')` instead of the `source` join (`:288-293`);
  - `_packages(upstream_id)` (`:387`) = packages of enabled apps whose bundle has this upstream (be-apps table);
  - CLI pre-pull loop (`:435-440`) = `engine_cli('morphe')` only.
- `inspect.sh` (`src/build/inspect.sh`), `parse_list_patches` (`:206`), `merge_bundle` (`:268`) stay: they are the only source of patch options and of the exact list in the downloaded `.mpp` (requirements §3). Result in `bundle_info` per (upstream, tag, package).
- `patch_list(bundle_id, package) -> (tag, files, patches | None)` replaces `_bundle` (`w/catalog.py:585`); `get_patches`/`put_patches`/`stale_overrides`/`_type_ok` (`:597-689`) move to be-apps' enabled-app routes unchanged in logic.

## 5. Icons

Static files must not reference external URLs (`w/tests/test_ui_static.py:35-40`), and an untrusted catalog must not make browsers load third-party hosts. So the server proxies and caches:
- `GET /api/apps/{pkg}/icon` (kept, `w/catalog.py:798`) serves `DATA/icons/<file>`; on a miss it fetches **lazily in the request** (no 1207-icon background crawl):
  - URL = `store_app.icon_url`, else the Play page `og:image` (today's `fetch_icon`, `w/catalog.py:752`) for packages without a catalog icon (YouTube, YT Music). One fetcher, two URL sources.
  - Only when `icon_url != icon_tried_url` (a failure is not retried until the URL changes or the user presses refresh, `POST /api/apps/{pkg}/icon/refresh` kept, which clears `icon_tried_url`).
  - https only; host allowlist `ICON_HOSTS = {play-lh.googleusercontent.com, lh3.googleusercontent.com, lh3.ggpht.com, raw.githubusercontent.com, avatars.githubusercontent.com}` checked on the final URL (redirects to other hosts refused); ≤256 KiB (`http_get(max_bytes=)`); content-type png/webp/jpeg; timeout 10 s. Covers 1183 of 1185 icons. Other hosts → 404 → UI letter tile.
  - File name `<package>.<ext>` (package validated); response `cache_seconds=86400`.
- Deleted: `_icon_loop`, `_icon_wake`, `start()` (`w/catalog.py:783-796`); the Play title scrape writing `app.display_name` (`:776-777`) goes, names come from `store_app`.

## 6. Deleted vs reused

Deleted (with their tests and docs):
- `catalog-seed.json`; `seed_import`, `export_seed`, `seed_text`, `SEED_TABLES`, `JSON_COLS`, `BOOL_COLS`, `DAILY_SCHEDULE`, `_to_db`, `_from_db` (`w/catalog.py:816-881`); boot call `w/__main__.py:34-35`; `export-seed` command and USAGE (`w/__main__.py:6,54-57`); `docs/Frequently Asked Questions.md:23-25`; `README.md:10-11` rewritten; `w/tests/test_catalog_seed.py`. The seed's APKMirror/APKPure URLs (~40 apps) are copied once into `app` by the migration (be-apps/be-api own that SQL).
- `source` table and every source helper: `_source`, `_source_fields`, `list/post/put/delete_source` (`w/catalog.py:343-427`), `_engine_of_source` (`:249`), `xposed_source_rules` (`:305`, its rule moves into `add_custom`), `ENGINES` kind map shrinks to `max_sigbypass` (be-apps, used by `validate_engine_options`).
- `upstream.list_upstreams`, `post_check`, `check_all`, `_all_rows`, `post_pull` and their routes (`w/upstream.py:174-196,462-467,509-511`): upstream rows become internal.
- `upstream.affected_items` (`:140`), `item_update_jobs` (`:470`), `post_item_update` (`:496`), the `pressed` mechanism (`:423-455`): replaced by be-apps' global run.
- Play icon loop (§5).
- Orphan `upstream` rows: migration deletes every `upstream` row not referenced by `bundle.upstream_id`, `project.upstream_id` or `ENGINE_CLI`, plus their `DATA/upstream/<id>/` dirs (boot-time one-off in be-apps' migration step, since `db.migrate` has no Python hook).

Reused unchanged: `upstream.http_get` (+ `max_bytes`), `opener`, `fetch`, `select_release`, `match_assets`, `_check`, `check`, `_download`, `_prune` (+ `bundle_info` prune), `_fetch_row`, `inspect_row` (CLI lookup changed), `parse_list_patches`, `merge_bundle`, `tag_dir`, `gh_headers`; `catalog.version_key`, `upstream_row`, `get_icon`, `post_icon_refresh`; `src/build/inspect.sh`; `bundle_info` table.

Module layout: catalog code (feeds, parsers, apply, sources, icons) stays in `w/catalog.py`, which loses ~500 lines of groups/sources/items/seed (items go to be-apps' module). No new module.

## 7. Tests (committed; hermetic, ≤1 s, no network)

New `w/tests/test_catalog_feed.py`, fixture `tests/fixtures/catalog/bundles-mini.json` (~40 lines, hand-made, covering every dialect in §0) and `patches-list-mini.json`:
1. `parse_official` accepts all dialects: both patch key pairs, three compat shapes, target without version, `targets: null`, universal patches → expected typed records (core parsing logic).
2. `parse_official` rejects: missing `bundles`, out-of-range compat index, bad package name, non-https iconUrl — message names path, expected, actual (fail-loud contract).
3. Refresh keeps last good copy: fake `http_get` returns 200 valid → rows; then 200 malformed → rows unchanged, `catalog_feed.last_error` set, ETag unchanged; then 304 → no apply (data integrity).
4. Apply keeps ids and enabled bundles: a bundle chosen by an enabled app and dropped from the feed stays with `listed=0`; an unchosen dropped bundle is deleted; ids of kept bundles unchanged (data integrity).
5. De-dup by rank: same repo in builtin/custom and official → one row with the better origin (multi-catalog seam).
6. Icon fetch refuses a host outside `ICON_HOSTS` and a redirect off the allowlist, and caps size (security).
7. `parse_patches_list` on the mini fixture → bundle_app/bundle_patch rows incl. universal (core parsing).
Kept: `test_catalog_inspect.py` (parser of CLI output). Rewritten by owners of the old model: `test_catalog_upstream.py` (check/pull/download parts stay, source/item fan-out parts go), `test_catalog_xposed.py` (→ custom module sources in test 2-style cases inside `test_catalog_feed.py`, then the old file is deleted). Deleted: `test_catalog_seed.py`.
Not committed: a one-off run of `parse_official` against the live 2.8 MB copy (scratch, `.claude/tmp/be-catalog/`), because the live file changes and would make the suite depend on a snapshot.

## Interfaces

Agreed with be-apps (`.claude/tmp/be-apps/interface-proposal.md` "To be-catalog") and be-api (`.claude/tmp/be-api/interfaces-proposal.md` "From be-catalog") on 2026-10-26.

Consumed by be-apps (enabled apps, build, global run):
- `item.bundle_id INTEGER NOT NULL REFERENCES bundle(id)`; `item` keeps its ids (be-apps). `bundle.id` is an integer and never changes for a bundle an enabled app uses. Every catalog bundle has a row (browsing), so the row exists before enable; `bundle.upstream_id` is filled by `ensure_upstream`.
- No `bundle.cli_upstream_id` / `bundle.keystore` columns: CLI and keystore depend only on the engine. `catalog.engine_cli(engine) -> upstream_id` (rows 1/19/20 kept); keystore = be-apps constant per engine (`morphe.keystore`, `lspatch-signing.p12`, `fiorenmas.ks`).
- `catalog.refresh(log)` — step 1 of the global run. Applies every feed/list that succeeded, records each failure (`catalog_feed.last_error`, `bundle.list_error`), keeps last good copies, then raises `CatalogRefreshFailed("<feed or repo>: <error>; ...")` if anything failed. Holds `_refresh_lock`; a concurrent caller gets `CatalogRefreshBusy`.
- `catalog.ensure_upstream(bundle_id) -> upstream_id` — on enable or bundle change; `HttpError(409)` for GitLab bundles.
- `catalog.patch_list(bundle_id, package) -> (tag, files, patches | None)` — reads `bundle_info` of the downloaded tag; build-time truth (be-apps resolves auto version from it in Python).
- `catalog.bundle_versions(bundle_id, package) -> list[str]` — browse versions, `[]` = none listed.
- `upstream._packages(upstream_id)` = `SELECT DISTINCT i.package FROM item i JOIN bundle b ON b.id = i.bundle_id WHERE b.upstream_id = ?` (all items on the bundle, enabled or not, as today).
- Names and icons: owned here. `store_app.name/category/icon` for every targeted package; `app.display_name`, `app.icon_file`, `app.icon_fetched_at` are dropped. Label = `item.display_name` (user override, be-apps) or `store_app.name` or the package.
- Migration (be-apps owns SQL + boot step): built-in bundle row on upstream 2; keep upstream rows 1/19/20; delete `upstream` rows not referenced by `bundle.upstream_id`, `project.upstream_id` or `engine_cli`.

Consumed by be-api (HTTP shapes; be-api owns routes):
- `catalog.market_apps() -> [{package, name, category, description, has_icon, origins[], bundle_count, newest_version | None, updated_at | None}]` (`updated_at` = newest `bundle.updated_at` among bundles targeting the app)` — full list; be-api filters/searches/sorts in memory. Icons are served only via `GET /api/apps/{pkg}/icon` (no external `icon_url` leaves the server).
- `catalog.app_bundles(package) -> [{bundle_id, origin, host, repo, name, author, stars, updated_at, engine, listed, selectable, unselectable_reason | None, versions[], patches[{name, description, default}]}]`; `default` is `bool | None`; unknown package → `KeyError`.
- `catalog.feed_status() -> [{origin, last_ok_at, last_attempt_at, last_error, bundle_count, app_count, duplicates[]}]` for each feed, plus `{origin: 'builtin'|'custom', repo, list_tag, last_error}` per source.
- `catalog.refresh()` run synchronously by the route (≤30 s fetch timeout per feed); busy → 409.
- `catalog.list_custom()`, `catalog.add_custom(url, engine, asset_regex=None, packages=())` (no keystore field; `asset_regex` and `packages` only for `lspatch`/`npatch`), `catalog.delete_custom(bundle_id)`.
- be-api reads `store_app.name` to rank APKMirror search candidates.

## Open questions for the user

1. **Bundles hosted on GitLab** (7 of 297 in the catalog). Relume can only download releases from GitHub today. Options: (a) list them in Marketplace but not selectable, with the reason shown; (b) add GitLab release download now (new API client, token handling, tests). Recommendation: (a); add GitLab when a user wants one of those bundles.
2. **Custom Morphe repos without `patches-list.json`.** Repos made from the official template publish this file, others may not. Options: (a) refuse to add such a repo with a clear message; (b) download its release and list patches with the patch CLI (slower add, needs the CLI download first). Recommendation: (a) now, (b) when it happens.

## Uncertainties

- Catalog `branch` (1 bundle, `dev`): mapping to the GitHub `prerelease` channel is a guess; the site has no schema doc.
- Bundle `default` missing for 1361 patches: shown as "unknown" in Marketplace; the real default comes from the downloaded bundle once enabled.
