# App Manage — implementation plan

Spec: `docs/superpowers/specs/2026-11-20-app-manage-design.md`. Preflight: `docs/logs/2026-11-20-app-manage-preflight.md`. Probe: `docs/logs/2026-11-20-play-url-probe.md`. Mockup: `docs/mockups/app-manage.html`. Contract: `docs/logs/2026-11-20-app-manage-contract.md`. Base: `8854f15`.

Everything in §1 is frozen. A task that needs a change to §1 stops and reports `BLOCKED` to the lead; it does not change the interface on its own.

## 0. Facts verified in code that change the preflight

| # | Fact | Where | Effect |
|---|---|---|---|
| F1 | `jobs.kind` has `CHECK (kind IN ('build','upstream','ota'))` (last rebuilt in 010 §6). A new job kind needs a jobs table rebuild, not only `jobs.OPTION_DEFAULTS`. | `schema/010_marketplace.sql:130-144`, `jobs.py:17-21,396` | Migration 024 rebuilds `jobs` (pattern 010 §6, `PRAGMA foreign_keys=OFF`). |
| F2 | Every mutating request must be `Content-Type: application/json`, body ≤ 1 MiB. | `server.py:216-219,23` | The icon upload is JSON with base64 PNG. No multipart. |
| F3 | The device row has no density column, the companion does not report density, and the apkeep profiles are fixed per ABI. | `schema/004,008`, `Inventory.kt:deviceFacts`, `build.py:19-20` | The profile is picked by ABI only (§1.8). The density and language splits come from the profile (`px_9a`: `config.xxhdpi`, `config.en`). Known limitation, see §5. |
| F4 | The phone's HTTP read timeout is 60 s; one Play probe answer may take up to `playversion.PROBE_TIMEOUT` = 120 s. | `RelumeApi.kt:321`, `playversion.py:22` | The action start call uses its own 180 s read timeout (§1.4). |
| F5 | ADB has no reader for labels or icons. | `devices.py`, `ota.py:_present` | An app that only ADB devices have shows the package name and a letter tile. |
| F6 | `/api/me/*` routes demand a device token; `auth.SESSION_ONLY` blocks device tokens on listed routes; `/api/me/*` routes are denied to legacy tokens by default. | `server.py:228-237`, `auth.py:19-30` | New web routes go into `SESSION_ONLY`; new companion routes need no legacy entry. |
| F7 | `CHANGES['ota'] = ('devices',)`: `POST /api/me/sync` publishes `devices` only. | `server.py:21-23` | `CHANGES['ota']` becomes `('devices', 'appmanage')`. |
| F8 | `apkeep -o device=<p>,probe=session` with stdin `<versionCode>` answers `{mode:'delivery', vc, ok, url, splits[{name,url}]}`; `PlayClient._ask` already parses it. `details` answers `versionCode`. | `playversion.py:50-123`, probe log | Add one method to `PlayClient` (§1.8). |

## 1. Frozen interfaces

### 1.1 Migration `local/webui/schema/024_appmanage.sql` (exact)

```sql
-- App Manage (spec 2026-11-20-app-manage-design §1): per-device app inventory, inventory report revision, device
-- action queue, and job kind appmanage. Foreign keys are off for the jobs rebuild (pattern 010 §6).
PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE jobs_new (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('build','upstream','ota','appmanage')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','success','failed','cancelled')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','schedule','upstream','after_build')),
  schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
  target TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '{}',
  reason TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
INSERT INTO jobs_new(id, kind, status, trigger, schedule_id, target, options, reason, created_at, started_at, finished_at)
  SELECT id, kind, status, trigger, schedule_id, target, options, reason, created_at, started_at, finished_at FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE INDEX jobs_kind_id ON jobs(kind, id);

-- user apps a device reported; separate from device_packages (the tracked OTA record). Sizes: all three known, or all
-- NULL with size_reason (never 0 for unknown).
CREATE TABLE device_apps (
  device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  package TEXT NOT NULL,
  label TEXT,                                  -- NULL = not reported (ADB)
  version_code INTEGER NOT NULL CHECK (version_code >= 0),
  version_name TEXT,
  app_bytes INTEGER CHECK (app_bytes >= 0),
  data_bytes INTEGER CHECK (data_bytes >= 0),
  cache_bytes INTEGER CHECK (cache_bytes >= 0),
  size_reason TEXT,
  icon_sha256 TEXT CHECK (icon_sha256 IS NULL OR length(icon_sha256) = 64),
  reported_at TEXT NOT NULL,
  PRIMARY KEY (device_id, package),
  CHECK ((app_bytes IS NOT NULL AND data_bytes IS NOT NULL AND cache_bytes IS NOT NULL AND size_reason IS NULL)
      OR (app_bytes IS NULL AND data_bytes IS NULL AND cache_bytes IS NULL AND size_reason IS NOT NULL)));

-- one row per device that reported an inventory: rev counts accepted reports (the companion's diff base)
CREATE TABLE device_app_reports (
  device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
  rev INTEGER NOT NULL CHECK (rev >= 1),
  reported_at TEXT NOT NULL);

CREATE TABLE device_actions (
  id INTEGER PRIMARY KEY,
  device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  package TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('install','upgrade','remove')),
  source TEXT CHECK (source IN ('build','play_direct','play_page')),         -- NULL for remove
  ref_kind TEXT CHECK (ref_kind IN ('item','project')),                     -- set only for source build
  ref_id INTEGER,
  state TEXT NOT NULL CHECK (state IN ('queued','delivered','running','done','failed','cancelled')),
  error_code TEXT,
  error_text TEXT,
  job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,                    -- last appmanage job that ran it (ADB)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((action = 'remove') = (source IS NULL)),
  CHECK ((source IS 'build') = (ref_kind IS NOT NULL AND ref_id IS NOT NULL)),
  CHECK (state NOT IN ('failed','cancelled') OR error_code IS NOT NULL));
CREATE UNIQUE INDEX device_actions_open ON device_actions(device_id, package) WHERE state IN ('queued','delivered','running');
CREATE INDEX device_actions_device ON device_actions(device_id, id DESC);

COMMIT;
PRAGMA foreign_keys=ON;
```

Delete and adopt (§1.10) rely on `ON DELETE CASCADE`; `db.conn()` turns foreign keys on.

### 1.2 Store API `local/webui/appmanage_store.py` (owner R1-A)

All functions take plain values, write in one `db.tx()` each, raise `server.HttpError` with expected/actual/device/package text on rule violations, and call no `events.changed` (callers publish).

```python
OPEN = ('queued', 'delivered', 'running')
ERROR_CODES = frozenset(devices.APP_ERROR_CODES) | {
    'PLAY_LOOKUP_FAILED', 'PLAY_DOWNLOAD_FAILED', 'NO_PLAY_PROFILE', 'DEVICE_OFFLINE', 'ADB_ERROR', 'TIMEOUT',
    'UNINSTALL_FAILED', 'ACTIVITY_START_FAILED', 'NOT_INSTALLED', 'REPLACED', 'CANCELLED_BY_USER', 'CANCELLED',
    'DEVICE_ADOPTED'}
ICON_DIR = db.DATA / 'icons' / 'appmanage'          # <sha256>.png, content-addressed
ICON_MAX_BYTES = 256 * 1024
PRIMARY_KEY = 'appmanage_primary_device_id'         # settings key, JSON int or null

def parse_inventory(obj) -> Inventory              # boundary parse of the sync "inventory" object (§1.4), HttpError 400
def ingest(device_id, inv: Inventory) -> dict      # -> {'rev': int, 'accepted': bool, 'need_icons': [sha256]}
def ingest_full(device_id, apps: dict) -> int      # ADB: replace all rows, rev+1; apps = {package: entry}
def inventory(device_id=None) -> dict              # {device_id: {package: entry}}; entry keys = §1.4 entry keys
def reports() -> dict                              # {device_id: reported_at}
def store_icon(sha256, png_bytes) -> None          # 400 on hash mismatch, not PNG (\x89PNG\r\n\x1a\n), > ICON_MAX_BYTES
def icon_path(sha256) -> pathlib.Path | None
def tracked_refs() -> dict                         # {package: {'kind','id','version_code','version_name'}} latest Relume build
def create_actions(package, action, device_ids, source=None) -> list[dict]   # §1.6 rules, all-or-nothing
def cancel(action_id) -> dict                      # DELETE route semantics (§1.3)
def pickup(device_id) -> list[dict]                # queued -> delivered, returns §1.4 action objects, oldest first
def start(device_id, action_id) -> dict            # delivered -> running; 404 foreign/unknown, 409 wrong state
def finish(device_id, action_id, result, error_code=None, error_text=None) -> dict  # delivered|running -> done|failed|cancelled
def finish_by_receipt(device_id, package, result, error_code, error_text) -> None    # build actions, from ota.post_install
def claim_for_job(action_id, job_id) -> dict | None   # ADB: queued -> running, None when no longer queued
def actions(device_id=None) -> list[dict]          # rows, newest first
def get_primary() -> int | None                    # setting, None when the device no longer exists
def set_primary(device_id) -> int | None           # 404 unknown, 409 legacy row; None clears
def move_device(c, from_id, to_id) -> None         # adopt helper, inside the caller's tx (§1.10)
```

`tracked_refs()`: items `item.package` and published artifact packages (`ota.tracked_packages()` rule); `version_code` = max `version_code` of `build.latest_artifact(item_id, abi)` over `catalog.ABIS` + `'universal'` (None when no build); projects: `projects.project_info(pid, [])` `version_code/version_name`. When a package maps to both an item and a project, the item wins.

### 1.3 Web HTTP API (module `local/webui/appmanage.py`, owner R2-A; all in `auth.SESSION_ONLY`)

| Method, path | Request | 200 response | Errors |
|---|---|---|---|
| `GET /api/appmanage` | – | matrix (below) | – |
| `PUT /api/appmanage/primary` | `{"device_id": int\|null}` | `{"primary_id": int\|null}` | 400 not int/null; 404 unknown device; 409 legacy device |
| `POST /api/appmanage/actions` | `{"package": str, "action": "install"\|"upgrade"\|"remove", "device_ids": [int,…], "source"?: "build"\|"play_direct"\|"play_page"}` | `{"actions": [{"id", "device_id", "state"}]}` | 400 field rules (§1.6); 404 unknown device; 409 running action / legacy device |
| `DELETE /api/appmanage/actions/{id}` | – | queued/delivered → `{"id", "state": "cancelled"}`; failed → `{"id", "deleted": true}` (dismiss) | 404 unknown; 409 `running`/`done`/`cancelled` ("expected queued, delivered or failed, got X") |
| `POST /api/appmanage/refresh` | `{}` | `{"job_ids": [int]}` | – |
| `GET /api/appmanage/icons/{sha}` | – | `FileResponse(png, cache_seconds=86400)` | 404 unknown or not 64 hex |

`POST /api/appmanage/refresh`: for every `kind='adb'` device, `jobs.enqueue('appmanage', {'device_id': id, 'refresh': True}, 'manual')`, plus `jobs.enqueue('appmanage', {'action_id': a}, 'manual')` for each of its `queued` actions. The page calls it once on load and from a Refresh button.

Matrix JSON (`GET /api/appmanage`), built by `appmanage_matrix.build(...)` (§1.7):

```json
{
  "primary_id": 3,
  "devices": [
    {"id": 3, "name": "Pixel 8", "kind": "app", "online": null, "reported_at": "2026-11-20T08:00:00Z"},
    {"id": 1, "name": "Tab", "kind": "adb", "online": true, "reported_at": null}
  ],
  "apps": [
    {"package": "com.spotify.music", "label": "Spotify", "icon": "<sha256>|null",
     "group": "off_primary", "source": "play_direct", "ref": null,
     "highest": {"version_code": 148387875, "version_name": "9.2.0.2053", "from": "device"},
     "cells": {
       "1": {"installed": {"version_code": 1, "version_name": "9.0", "app_bytes": 1, "data_bytes": 2, "cache_bytes": 3,
                           "size_reason": null, "outdated": true},
             "action": {"id": 7, "action": "upgrade", "source": "play_direct", "state": "queued",
                        "error_code": null, "error_text": null, "created_at": "…", "updated_at": "…"}},
       "3": {"installed": null, "action": null}
     }}
  ]
}
```

- `devices`: legacy rows excluded; primary first, then by `name` (case-folded), then `id`. `online`: ADB = `devices.online_states()` value is `device`; app rows = `null`. `reported_at` = `device_app_reports.reported_at`.
- `apps[].cells` has one key per device in `devices` (string id). `source` = default install source: `build` when `ref` is set, else `play_direct`. `ref` = `{"kind","id"}` or null. `highest.from` ∈ `device`, `build`.
- `group` ∈ `off_primary`, `on_primary`.

### 1.4 Companion API delta (`/api/me/*`, device token)

**`POST /api/me/sync` request** — new optional key (absent = old app, nothing ingested, no actions picked up):

```json
"inventory": {
  "base_rev": 4,              // int, the rev of the last accepted report; null with full=true for the first report
  "full": false,              // true: apps is the whole list, removed must be []
  "apps": [ {"package": "com.x", "label": "X", "version_code": 12, "version_name": "1.2",
             "app_bytes": 1, "data_bytes": 2, "cache_bytes": 3, "size_reason": null,
             "icon_sha256": "<64 lowercase hex>|null"} ],
  "removed": ["com.y"]
}
```
Rules (`parse_inventory`): `package` matches `server.PACKAGE`, unique across `apps`+`removed`; `label` str 1–200 chars; `version_code` int ≥ 0; `version_name` str|null; sizes all ints ≥ 0 with `size_reason` null, or all null with `size_reason` str 1–200; `icon_sha256` `ota.SIGNER` pattern or null; at most 5000 apps.

Ingest: `full=true` → replace all rows of the device. `full=false` → accepted only when `base_rev` equals the stored rev; upsert `apps`, delete `removed`. Accepted → `rev = old+1`, `reported_at = now`. Not accepted → nothing stored. Legacy rows: ignored.

**`POST /api/me/sync` response** — two new keys (old apps ignore them: `ApiJson { ignoreUnknownKeys = true }`):

```json
"inventory": {"rev": 5, "accepted": true, "need_icons": ["<sha256>"]},   // null when the request had no inventory
"actions": [ {"id": 7, "package": "com.x", "action": "install", "source": "play_direct", "ref": null},
             {"id": 8, "package": "app.revanced.android.youtube", "action": "upgrade", "source": "build",
              "ref": {"kind": "item", "id": 4}},
             {"id": 9, "package": "com.y", "action": "remove", "source": null, "ref": null} ]
```
`accepted=false` → the phone sends a `full=true` report in a second `POST /api/me/sync` in the same sync run (once). `actions` = `pickup(device_id)`: every `queued` action of the device becomes `delivered` in this response, oldest first; delivered actions are never sent again. Only when the request carried `inventory`.

**`POST /api/me/icons`** `{"sha256": str, "png_base64": str}` → 200 `{"stored": true}`; 400 bad base64 / hash mismatch / not PNG / > 256 KiB. Idempotent.

**`POST /api/me/actions/{id}/start`** `{}` → `delivered` → `running`.
- `source=play_direct`: the server resolves the URLs now (§1.8) and answers `{"id": 7, "version_code": 148387875, "apks": [{"name": "base", "url": "https://play.googleapis.com/…"}, {"name": "config.arm64_v8a", "url": "…"}]}`. A lookup failure sets the action `failed`/`PLAY_LOOKUP_FAILED` and answers 502 `{"error": "<reason>", "code": "PLAY_LOOKUP_FAILED"}`. URLs are never stored or logged.
- other sources: `{"id": 9}`.
- 404 `action <id> not found for this device` when the id is unknown or belongs to another device (same answer: no existence leak). 409 when not `delivered`.
- The phone uses a 180 s read timeout for this call only.

**`POST /api/me/actions/{id}/result`** `{"result": "done"|"failed"|"cancelled", "error_code"?: str, "error_text"?: str}` → 200 `{"ok": true}`. `failed` needs `error_code` ∈ `ERROR_CODES`; `cancelled` stores `error_code='CANCELLED_BY_USER'` when absent. 404 as above; 409 when not `delivered`/`running` (replaced or cancelled on the web: the phone drops it).

**Build source on the companion**: the phone does not post a result. It runs the existing scoped plan (`GET /api/me/plan?items=<id>` or `?projects=<id>`) through `InstallRunner.start(..., Origin.BACKGROUND)`. `ota.post_install` calls `appmanage_store.finish_by_receipt(device_id, package, result, error_code, error_text)` after it stores the receipt: the device's open `build` action for that package becomes `done` (`installed`) or `failed` (`USER_REJECTED` → `cancelled`/`CANCELLED_BY_USER`).

### 1.5 SSE

`events.TOPICS` gains `'appmanage'`. `server.MODULES` gains `'appmanage'`; `CHANGES['appmanage'] = ('appmanage',)`; `CHANGES['ota'] = ('devices', 'appmanage')`. The frame stays `event: change` / `data: ["appmanage", …]`; the page calls `live(['appmanage', 'devices'], load)`. The ADB job handler calls `events.changed('appmanage')` after every state or inventory write.

### 1.6 Action state machine and rules

```
queued ──pickup (sync)──▶ delivered ──start──▶ running ──result──▶ done | failed | cancelled
  │                          │   └──────────result──────────────▶ done | failed | cancelled
  │ ADB job claim ───────────┼──────────────▶ running
  └─ web cancel / replace ──▶ cancelled ◀── web cancel / replace (delivered)
failed ── web DELETE ──▶ row deleted (dismiss)
```
- Create (`create_actions`), validated for every device before any write (all-or-nothing):
  - `package` matches `server.PACKAGE`; `device_ids` non-empty unique ints; each device exists (404) and is not legacy (409).
  - `remove`: `source` must be absent. `install`/`upgrade`: `source` default = `build` when `tracked_refs()` has the package, else `play_direct`; `build` needs a tracked ref (400 `source build needs a Relume-tracked package, <pkg> is not`); `play_direct`/`play_page` are allowed for any package.
  - An open `running` action for the same device+package → 409 `device <name> is running <action> of <pkg>: wait or cancel later`.
  - An open `queued`/`delivered` action → it becomes `cancelled`, `error_code='REPLACED'`, `error_text='replaced by action <new id>'`.
  - Assignments: `install`/`upgrade` with source `build` → `devices.assign_one(device_id, 'items'|'projects', ref_id)`; `remove` of a tracked package → `devices.unassign_one(...)` (both at create time, so the OTA planner and the background check never fight the action).
  - ADB device → `jobs.enqueue('appmanage', {'action_id': id}, 'manual')`; app device → stays `queued` until the next sync.
- Web cancel: `queued`/`delivered` → `cancelled`, `error_code='CANCELLED'`. A late phone result for it gets 409.
- Cell action shown by the matrix: the open action, else the newest action of the cell when it is `failed`; `done`/`cancelled` are not shown.

### 1.7 Matrix logic `local/webui/appmanage_matrix.py` (pure, owner R1-B)

```python
def build(devices, inventories, actions, tracked, primary_id) -> dict   # §1.3 JSON
def row_order(apps, primary_id, secondary_ids) -> list[str]             # packages in display order
def highest(package, inventories, tracked) -> dict | None               # {'version_code','version_name','from'}
```
Inputs: `devices` = list of `{id, name, kind, online, reported_at}` (already filtered and ordered per §1.3); `inventories` = `{device_id: {package: entry}}`; `actions` = list of action rows (dicts with the §1.3 action keys plus `device_id`, `package`); `tracked` = `tracked_refs()`; `primary_id` int|None.
- Packages = union of all inventories and of all packages with a shown action (§1.6 last rule).
- Total size of a cell = app+data+cache; unknown when sizes are null.
- Row order: group `off_primary` (not installed on the primary; all rows when `primary_id` is None) first, by the largest known total among the secondaries descending; then group `on_primary` by the primary's total descending. Unknown sizes sort after every known size in their group. `package` ascending breaks every tie.
- `highest` = max `version_code` over all devices' entries and `tracked[pkg].version_code`; `from='build'` only when the build is strictly higher than every device; `version_name` from that source.
- `outdated` = `installed.version_code < highest.version_code` (any column, the primary included).
- `label` = primary's label, else first non-null in `devices` order, else the package. `icon` = same order over `icon_sha256`.

### 1.8 Play URL resolution `local/webui/appmanage_play.py` (owner R1-D)

```python
PROFILE_BY_ABI = build.APKEEP_DEVICE      # reuse; no new profiles
def profile_for(dev) -> str               # first ABI of dev['abi_list'] -> profile; HttpError-free ValueError
                                          # "no apkeep profile for ABI <abi> of device <name>" (code NO_PLAY_PROFILE)
def resolve(package, dev, log=None) -> dict   # {'version_code': int, 'apks': [{'name','url'}]}; raises PlayLookupError
class PlayLookupError(Exception)          # .code in {'PLAY_LOOKUP_FAILED','NO_PLAY_PROFILE'}, message names package+device
```
Mapping rule: `profile = build.APKEEP_DEVICE[abis[0]]` with `abis = dev['abi_list'].split(',')` (the phone's preferred ABI); an ABI outside the map → `NO_PLAY_PROFILE`. `sdk` and density are not mapped (F3). `resolve`: `settings.google_play()` None → `PLAY_LOOKUP_FAILED` "Google Play credentials not set in Settings"; ini written with `settings.write_secret(db.KEY / f'apkeep-am-{uuid}.ini', …)` and deleted in `finally`; one `playversion.PlayClient` session: `details()` → version code, then the new `PlayClient.delivery(code) -> {'url', 'splits'}` (same `_ask(str(code), 'delivery', code)`, raises `VersionCodeError` when not ok); `apks = [{'name': 'base', 'url': url}] + splits`. Log lines carry host and split names only, never a URL query.

### 1.9 ADB job kind `appmanage` (owner R2-C, file `local/webui/appmanage_job.py`)

- `jobs.OPTION_DEFAULTS['appmanage'] = {}`; `appmanage.register()` calls `jobs.register('appmanage', appmanage_job.handle, appmanage_job.detail)`.
- Targets: `{"action_id": n}` or `{"device_id": n, "refresh": true}`.
- `handle(job, log)`:
  1. Refresh target: device gone or app row → `JobFailed`; `devices.reconnect`; offline → `JobFailed('DEVICE_OFFLINE')`; `apps = devices.app_inventory(dev, job.cancel_event)`; `appmanage_store.ingest_full(id, apps)`; `events.changed('appmanage')`.
  2. Action target: `claim_for_job(action_id, job.id)` None → return (cancelled or replaced meanwhile). Device offline → action back to `queued` (it re-runs at the next refresh), job reason `DEVICE_OFFLINE`, return.
  3. `build` → `ota.install_ref(job, dev, ref_kind, ref_id, log)`; `play_direct` → `appmanage_play.resolve`, then `pm install-create -r` / per APK HTTP GET streamed into `devices.adb_pipe(['shell','pm','install-write','-S',str(size),sid,name,'-'], resp, …)` / `pm install-commit sid` (`pm install-abandon sid` on any failure); `remove` → `adb uninstall <pkg>`; `play_page` → `adb shell am start -a android.intent.action.VIEW -d market://details?id=<pkg>`.
  4. Finish the action `done` or `failed` with an `ERROR_CODES` code and text "expected …, got … on <device> for <pkg>"; re-read the inventory (`app_inventory` + `ingest_full`) after install/upgrade/remove; `events.changed('appmanage')`.
- `detail(job_id)` → `{'action': <row or None>}`.
- ponytail: one worker thread for the kind, so ADB actions run one at a time across devices.

### 1.10 Device delete, adopt, legacy, primary

- `devices.delete_device`: rows in `device_apps`, `device_app_reports`, `device_actions` go by `ON DELETE CASCADE`; it also clears the primary setting when it named this device (`settings.put(PRIMARY_KEY, None, c)` in the same tx). A queued appmanage job of a deleted device finds no action and ends.
- `ota.adopt` (inside its tx, before `DELETE FROM devices WHERE id = me_id`): `appmanage_store.move_device(c, me_id, tid)`:
  - delete the ADB row's `device_apps` and `device_app_reports`; move the phone's rows to `tid`;
  - the ADB row's open actions → `cancelled`, `error_code='DEVICE_ADOPTED'`; move the phone's actions to `tid`;
  - primary setting equal to `me_id` → `tid`.
- `ota._leave_legacy` deletes a legacy row: legacy rows never ingest inventory nor get actions, so the cascade drops nothing.
- Primary: setting key `appmanage_primary_device_id`, JSON int or null, read by `get_primary()` (a dangling id reads as None).

### 1.11 ADB inventory reader (owner R1-C, in `devices.py`)

```python
def parse_user_packages(text) -> dict            # `pm list packages -3 --show-versioncode` -> {pkg: version_code}
def parse_version_names(text) -> dict            # `dumpsys package packages` -> {pkg: versionName}
def parse_diskstats(text) -> dict                # `dumpsys diskstats` -> {pkg: (app, data, cache)}; ValueError on bad shape
def app_inventory(dev, cancel_event=None) -> dict   # {pkg: entry per §1.4 keys, label None, icon_sha256 None}
def adb_pipe(args, src, *, serial, timeout, cancel_event=None, log=None) -> tuple[int, str]  # stdin streamed from src (file-like .read), same per-serial lock and AdbTimeout as adb()
```
`app_inventory`: diskstats missing, unparsable, or without the package → sizes null, `size_reason` = `"dumpsys diskstats: <why>"`. A failed `pm list` raises (no inventory written).

`ota.install_ref(job, dev, kind, ref_id, log) -> tuple[str|None, str]` (owner R1-C): `_present(dev)` → `_plan_for(dev, [ref_id] or [], [] or [ref_id], present)['steps']` → `_run_item`/`_run_project` like `handle` → `_present(dev)`; returns `(None, 'installed'|'up_to_date')` or `(error_code, text)` from the last `ota_installs` row. History rows carry the appmanage job id.

### 1.12 Android contract (frozen names)

- `api/Models.kt`: `InventoryApp`, `InventoryReport(baseRev: Long?, full: Boolean, apps, removed)`, `SyncReport(device, packages, inventory: InventoryReport? = null)`, `InventoryAck(rev: Long, accepted: Boolean, needIcons: List<String>)`, `DeviceAction(id: Long, packageName, action: ActionKind, source: ActionSource?, ref: ActionRef?)`, `SyncResponse(…, inventory: InventoryAck? = null, actions: List<DeviceAction> = emptyList())`, `ActionStart(id, versionCode: Long? = null, apks: List<PlayApk> = emptyList())`, `ActionResult(result, errorCode, errorText)`, `IconUpload(sha256, pngBase64)`. JSON names per §1.4.
- `api/RelumeApi.kt`: `uploadIcon(IconUpload)`, `startAction(id): ActionStart` (180 s read timeout), `reportAction(id, ActionResult)`.
- `inventory/Inventory.kt`: `SyncResult` gains `actions: List<DeviceAction>`; `sync()` sends the inventory, resends full once when `accepted=false`, uploads `needIcons`, stores the accepted rev + snapshot.
- `api/Prefs.kt`: `CheckInterval { OFF, M15, H1, H6, H24, DAILY }`, default unchanged; `CheckScheduler` maps `M15` to a 15-minute periodic request with `NetworkType.CONNECTED`.

## 2. Rounds and tasks

Shared git rules for every task: no `git stash` (incl. `stash list`), `reset`, `checkout --`, `clean`, `add -A`; workers do not commit (the lead commits with pathspecs). No shell `>`/`>>`; no `sleep`/polling; never `sqlite3 local/data/webui.db`; no docker except R2-E. Python tests: `cd local && python3 -m unittest webui.tests.<module>` (the form `scripts/test.py` uses, cwd `local`); each test ≤ 1 s.

### Round 1 (6 parallel tasks, disjoint files)

| Task | Owner files (only these) | Model |
|---|---|---|
| R1-A store | `schema/024_appmanage.sql`, `appmanage_store.py`, `tests/test_appmanage_store.py` | opus |
| R1-B matrix | `appmanage_matrix.py`, `tests/test_appmanage_matrix.py` | sonnet |
| R1-C ADB reader | `devices.py`, `ota.py` (`install_ref` only), `tests/test_appmanage_adb.py`, `tests/fixtures/device/appmanage/*` | sonnet |
| R1-D Play resolve | `appmanage_play.py`, `playversion.py` (`PlayClient.delivery` only), `tests/test_appmanage_play.py` | sonnet |
| R1-E web page | `static/appmanage.html`, `static/appmanage.js`, `static/app.css` (matrix block appended), `static/app.js` (`shell()` nav entry + one sprite symbol) | sonnet |
| R1-F Android report | `api/Models.kt`, `api/RelumeApi.kt`, `api/Prefs.kt`, `sync/CheckScheduler.kt`, `inventory/Inventory.kt`, new `inventory/AppInventory.kt`, new `inventory/InventoryDiff.kt`, `AndroidManifest.xml`, `ui/SettingsScreen.kt`, `ui/RelumeRoot.kt`, new `app/src/test/.../InventoryDiffTest.kt` | opus |

Paths without a prefix are under `local/webui/`; Android paths under `android/app/src/main/kotlin/io/github/jhangyu/relume/` (manifest under `android/app/src/main/`).

**R1-A store.** Deps: none (it uses `devices.assign_one/unassign_one`, `build.latest_artifact`, `projects.project_info`, `settings` as they are). Steps: write 024 exactly as §1.1; implement §1.2 and §1.6; icons in `ICON_DIR` written atomically (`tmp` + `os.replace`). Tests (`test_appmanage_store.py`): migration applies on the template DB (`device_apps`, `device_actions`, `jobs` accepts `kind='appmanage'`); full ingest, diff ingest with matching `base_rev`, stale `base_rev` → `accepted=False` and nothing stored; `need_icons` lists only missing hashes; replace rule (queued → cancelled `REPLACED`), running → 409, all-or-nothing on a bad device id; pickup queued → delivered once; start/finish state transitions and 409 on wrong state; another device's action id → 404; `finish_by_receipt`; build source writes the assignment, remove of a tracked package deletes it; cancel and failed-dismiss; `move_device`; delete cascade. Acceptance: `(cd local && python3 -m unittest webui.tests.test_appmanage_store webui.tests.test_core_db)` green; `python3 scripts/test.py --legs python` green.

**R1-B matrix.** Deps: none (pure, plain dicts). Steps: implement §1.7. Tests: row order (both groups, unknown sizes last, package tie-break, no primary); `highest` from device vs build; `outdated` on the primary column; label/icon fallback order; shown action (open beats failed, done hidden). Acceptance: `(cd local && python3 -m unittest webui.tests.test_appmanage_matrix)` green; module imports nothing but stdlib.

**R1-C ADB reader.** Deps: none. Steps: §1.11 parsers + `app_inventory` + `adb_pipe` in `devices.py`; `delete_device` clears the primary setting (§1.10, use the literal key `'appmanage_primary_device_id'` via `settings.put` to avoid importing the store); `ota.install_ref`. Fixtures: text samples of the three commands (format as documented by AOSP: `Package Names: [...]`, `App Sizes: [...]`, `App Data Sizes: [...]`, `Cache Sizes: [...]`). Tests: each parser on fixtures incl. a bad shape → ValueError; `app_inventory` with `FakeAdb` incl. missing diskstats → `size_reason`; `adb_pipe` streams bytes into a fake adb that echoes stdin length; `install_ref` with `FakeAdb` for one item. Acceptance: `(cd local && python3 -m unittest webui.tests.test_appmanage_adb webui.tests.test_device_adb webui.tests.test_device_otajob webui.tests.test_device_routes)` green.

**R1-D Play resolve.** Deps: none. Steps: §1.8. Tests: `profile_for` for each ABI in `build.APKEEP_DEVICE`, first-ABI rule, unknown ABI → `NO_PLAY_PROFILE`; `resolve` with a fake `apkeep` executable in a temp dir (prints the JSON lines of F8) → `apks` order base first; no credentials → `PLAY_LOOKUP_FAILED`; the ini file is gone after success and failure. Acceptance: `(cd local && python3 -m unittest webui.tests.test_appmanage_play webui.tests.test_playversion)` green.

**R1-E web page.** Deps: none (builds against the §1.3 JSON). Steps: page from the mockup (dense grid, sticky header, app + primary columns, resizable widths in `localStorage` wrapped in try/catch, popups not clipped, primary selector → `PUT /api/appmanage/primary`, cell buttons → `POST /api/appmanage/actions`, batch dialog with device checkboxes, cancel/dismiss → `DELETE`, Refresh → `POST /api/appmanage/refresh` (also once on load), icons from `/api/appmanage/icons/<sha>` else `iconTile` letter tile, "waiting for phone sync" copy for app rows, offline shows `reported_at`); `live(['appmanage','devices'], load)`; shared components only; matrix CSS appended to `app.css` under a `/* App manage */` block; sidebar `<a href="/appmanage.html">` after Devices, icon from `python3 ~/ui-kits/find-icon.py` (Fluent 20 regular), `html` tag for all markup. A fixture JSON + static stub for manual viewing stays in scratch (not committed). Acceptance: `(cd local && python3 -m unittest webui.tests.test_ui_static)` green; `node --check local/webui/static/appmanage.js` exit 0; `grep -c 'appmanage.html' local/webui/static/app.js` ≥ 1.

**R1-F Android report.** Deps: none (server shapes frozen in §1.4/§1.12). Steps: DTOs and API calls of §1.12 (incl. the R2 ones: `startAction`, `reportAction`); `AppInventory.read(context)`: user apps = `ApplicationInfo.FLAG_SYSTEM` unset, label, version, sizes via `StorageStatsManager.queryStatsForPackage` when usage access is granted (`AppOpsManager.OPSTR_GET_USAGE_STATS`), else null + `size_reason "usage access not granted"`, icon rendered to a 96×96 PNG + sha256; `InventoryDiff` (pure): previous snapshot + rev vs current → `InventoryReport`; snapshot file in app storage written only after `accepted`; `PACKAGE_USAGE_STATS` (`tools:ignore="ProtectedPermissions"`); Settings row "Usage access" with a button to `Settings.ACTION_USAGE_ACCESS_SETTINGS`, re-read on resume; `M15` interval + label "15 min". Tests: `InventoryDiffTest` (first report full, unchanged → empty diff, changed/removed, rev mismatch → full). Acceptance: `ANDROID_HOME=<sdk> python3 scripts/test.py --legs android` green; `android/gradlew -p android assembleDebug` exit 0.

### Round 2 (5 tasks; R2-E runs after R2-A..D report)

| Task | Owner files | Model |
|---|---|---|
| R2-A routes + wiring | `appmanage.py`, `server.py`, `events.py`, `auth.py`, `tests/test_appmanage_api.py` | opus |
| R2-B sync + adopt | `ota.py`, `tests/test_appmanage_sync.py` | opus |
| R2-C ADB job | `appmanage_job.py`, `jobs.py` (OPTION_DEFAULTS only), `tests/test_appmanage_job.py` | sonnet |
| R2-D Android executor | new `action/ActionExecutor.kt`, new `action/ActionRules.kt`, new `action/ActionStore.kt`, `install/Installer.kt`, `install/InstallResultReceiver.kt`, `sync/Notifications.kt`, `sync/CheckWorker.kt`, `ui/AppModel.kt`, new `app/src/test/.../ActionRulesTest.kt` | opus |
| R2-E integration + live list | `docs/logs/2026-11-20-app-manage-live-checks.md` (new); no source edits (failures go back to the owner task) | sonnet |

**R2-A.** Deps: R1-A, R1-B, R1-E merged. Steps: routes of §1.3 and the companion routes `POST /api/me/icons`, `POST /api/me/actions/{id}/start` (calls `appmanage_play.resolve` for `play_direct`), `POST /api/me/actions/{id}/result` in `appmanage.ROUTES`; `register()` (job kind registration needs R2-C's module: import `appmanage_job` lazily inside `register`); `server.MODULES`/`CHANGES` per §1.5; `events.TOPICS`; `auth.SESSION_ONLY` += the five web routes of §1.3 except the icon GET. Tests: device token on `POST /api/appmanage/actions` → 403; token A on `/api/me/actions/<B's id>/start|result` → 404; legacy token on `/api/me/icons` → 403; matrix route returns §1.3 keys; `PUT primary` 404/409; create → SSE topic `appmanage` moved (`events._last`). Acceptance: `(cd local && python3 -m unittest webui.tests.test_appmanage_api webui.tests.test_core_router webui.tests.test_core_auth webui.tests.test_core_sse webui.tests.test_legacy_client)` green.

**R2-B.** Deps: R1-A, R1-C. Steps: `ota._parse_sync` parses optional `inventory` via `appmanage_store.parse_inventory`; `ota.sync` ingests (non-legacy only) and adds `inventory` + `actions` to the response (`_sync_json` callers unchanged for `GET /api/me`); `post_install` → `finish_by_receipt`; `adopt` → `move_device` (§1.10). Tests: old-app body (no `inventory`) → response `inventory: null`, `actions: []`, nothing picked up; full then diff then stale base; pickup only once; legacy row ignored; receipt finishes build action; adopt moves inventory/actions and cancels the ADB row's open actions. Acceptance: `(cd local && python3 -m unittest webui.tests.test_appmanage_sync webui.tests.test_device_app webui.tests.test_device_ota webui.tests.test_legacy_client)` green.

**R2-C.** Deps: R1-A, R1-C, R1-D. Steps: §1.9. Tests with `FakeAdb` and a fake HTTP source (local `http.server` on 127.0.0.1 in-process, no external network): remove done; offline → action back to queued; claim of a cancelled action → no-op; play_direct streams 2 APKs into install-write and commits, failure → abandon + `PLAY_DOWNLOAD_FAILED`; job log contains no `token=`. Acceptance: `(cd local && python3 -m unittest webui.tests.test_appmanage_job webui.tests.test_core_jobs)` green.

**R2-D.** Deps: R1-F. Steps: after every `Inventory.sync` (CheckWorker, AppModel on open; `InstallWorker` unchanged) run `ActionExecutor.run(context, api, result.actions)`; `ActionStore` persists delivered ids until their result is posted (retry posting at the next run); `build` → `api.plan(items|projects)` + `InstallRunner.start(..., Origin.BACKGROUND)` (no result post); `play_direct` → `startAction` → one `PackageInstaller` session, each APK streamed from its URL into `session.openWrite(name, 0, size)` (no file cache) → commit with a new receiver action `ACTION_APP_ACTION` carrying the action id; `remove` → uninstall intent with the same receiver action; `play_page` → notification with `market://details?id=<pkg>` (fallback `https://play.google.com/store/apps/details?id=<pkg>`) then result `done`; status mapping in `ActionRules` (pure). Tests: `ActionRulesTest` (PackageInstaller status → result/code incl. `STATUS_FAILURE_ABORTED` → cancelled, unknown action kinds rejected). Acceptance: `ANDROID_HOME=<sdk> python3 scripts/test.py --legs android` green; `assembleDebug` exit 0.

**R2-E integration.** Deps: R2-A..D signed off. Steps: on the merged tree run (1) `python3 scripts/test.py` (all legs, `ANDROID_HOME` set) → exit 0, total ≤ 90 s, no test > 1 s, skip count 0; (2) the Linux gate from `CLAUDE.md` → exit 0; (3) `python3 scripts/ci_local.py` → exit 0; (4) grep checks: `grep -n "'appmanage'" local/webui/events.py local/webui/server.py local/webui/jobs.py` non-empty, `grep -rn "token=" local/webui/appmanage*.py` empty; write the live check list (§4) into `docs/logs/2026-11-20-app-manage-live-checks.md` with one row per check and an empty result column. Acceptance: artifact with command, self-captured `RC=$?`, decisive output line and HEAD hash for (1)–(4).

## 3. Test plan (project policy)

Committed (core logic only, each ≤ 1 s, hermetic): `test_appmanage_store.py` (queue rules, ingest diff, cascade/adopt helpers), `test_appmanage_matrix.py` (row order, outdated), `test_appmanage_adb.py` (parsers), `test_appmanage_play.py` (profile mapping, fake apkeep), `test_appmanage_api.py` (token-scoped authorization), `test_appmanage_sync.py` (sync ingest + pickup), `test_appmanage_job.py` (ADB job rules), Android `InventoryDiffTest`, `ActionRulesTest`. Not committed: UI/browser/screenshot tests, the Play probe, red-to-green proofs (scratch + `docs/logs/`). Budget: the new Python modules add < 3 s to a leg that runs in parallel workers; the suite stays ≤ 90 s (checked by R2-E).

## 4. Live checks (user runs the device steps; R2-E writes the list)

1. Server redeployed with migration 024; `GET /api/appmanage` lists every non-legacy device.
2. Companion updated; open it → the page shows the phone's user apps with labels, icons and sizes (sizes "unknown: usage access not granted" before the grant, real sizes after granting usage access in Settings).
3. ADB device: Refresh → its user apps appear with version names; sizes or a `dumpsys diskstats` reason.
4. Set the primary; row groups and order follow §1.7.
5. Remove an app on the companion phone → system confirm dialog → cell clears after the result and the next sync.
6. Remove an app on the ADB device → cell clears.
7. Install a Relume-tracked app on another device → assignment appears on the Devices page; install completes (ADB: job; companion: install sheet / background run).
8. **Cross-IP Play URL**: companion phone on mobile data (Wi-Fi off), install a non-tracked app with `play_direct` → installed. Failure with an HTTP 403 from `play.googleapis.com` means the URLs are IP-bound → stop: the in-app Play client is the follow-up (out of scope).
9. `play_direct` on the ADB device → installed (download streamed through the server).
10. `play_page` on the companion → notification opens the Play Store page.
11. 15-minute interval selectable in the companion Settings; the default is unchanged.
12. Queue an action for an offline companion → "waiting for phone sync" → runs at the next sync.

## 5. Known limitations and decisions (decided, reported to the user)

Decisions: build-source assignment is written/removed at action create; play sources are allowed for tracked packages; companion build actions finish through the existing install receipt; delivered actions are sent once and can be cancelled/replaced on the web; preflight §0.1 (no 15-min interval) is settled by the spec (`M15`). Uncertain: the `dumpsys diskstats` fixture follows the AOSP format, not a device capture (live check 3).


- Play splits come from the ABI profile: density `xxhdpi` and language `config.en` for `px_9a`. Apps still install; a phone with a non-English locale may lack its language split (untranslated UI) until a per-locale profile exists.
- ADB-only apps show no label or icon (F5).
- ADB actions run one at a time across devices (single worker per job kind).
- An ADB action on an offline device stays queued until the next Refresh (page load or button).
- Icons are content-addressed by sha256 instead of package + version code (same effect, dedups across devices).
