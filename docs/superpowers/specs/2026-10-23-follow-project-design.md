# Follow project — binding design spec (2026-10-23)

Inputs: requirements `docs/logs/2026-10-23-follow-project-brief.md`; architecture B `docs/logs/2026-10-23-follow-project-arch-b.md` (base); architecture A `docs/logs/2026-10-23-follow-project-arch-a.md` (two parts only: APK facts read with APKEditor `info`, ABI read from `lib/<abi>/` zip entries); user decisions listed in §0.2; mockup `docs/superpowers/mockups/follow-project/option-a.html`.

Paths without a directory prefix are relative to `local/webui/`. Every statement about current code cites `file:line` at commit `4cc414a`.

---

## 0. Convergence contract (frozen; only the user can change it)

### 0.1 End state (one sentence)

The web UI has a "Followed projects" page (mockup option A) where the user follows GitHub repos; the upstream schedule checks and downloads their release APKs, every APK is verified (signer, package, version, ABI) before the release is committed, and the APKs install to assigned phones through the OTA job, with the old "dependency app" model fully replaced by followed projects.

### 0.2 Binding user decisions

1. UI = `option-a.html` (dense table + detail pane). §8 maps every element to an API call or state field, or marks it dropped with a reason.
2. A signer change blocks every install path of that project (auto-push, scheduled OTA, manual Install, and items that depend on it) until the user presses "Trust new signer".
3. New notifier event `project.signer_changed`, in `notify.EVENTS` and in the settings UI event list. Users with a saved events list must tick it (§11).
4. The migration assigns MicroG RE directly to every phone that has an assigned item depending on it.
5. "Update available" = the downloaded tag is newer than the tag installed on the phone.
6. Before the first boot on the live stack, back up `local/data/webui.db` (deploy step, §11).
7. Package name and version come from each downloaded APK via APKEditor `info` in the isolated runner. The user does not type the package.
8. ABI of each APK comes from its `lib/<abi>/` zip entries. No `lib/` dir, or more than one ABI, = `universal`. Pick order: the phone's full ABI list in order, then `universal`.
9. Everything else follows architecture B: server-side `derive_regex`, Python `re` as the only matcher; verify every APK before the tag dir is committed; reject releases with mixed signers; projects skip the stable `dev` asset filter; pull enqueues auto-push.

### 0.3 In scope

- Migration `schema/005_follow.sql` (§3.1), seed change (§3.3).
- New module `projects.py`: project store, `/api/projects*` routes, release verification, signer pinning, auto-push enqueue.
- Upstream pipeline changes (`upstream.py`), notifier event (`notify.py`), item dependency field (`catalog.py`).
- OTA planner/handler/read model and device assignment (`ota.py`, `devices.py`), project install route.
- Frontend: new `projects.html` / `projects.js`; nav link on every page; Devices, item, schedules and settings page changes.
- Deletion of every trace of the dependency-app model (§12).
- Spec/API-shape doc updates that point at this file.

### 0.4 Out of scope

- Non-GitHub sources; building or patching followed APKs.
- Byte-level download progress (mockup progress bar, §8 row 33).
- Discarding a quarantined release (mockup "Discard vX", §8 row 31).
- Per-project check schedules (`project_ids` in schedule targets). `{"all": true}` covers projects (`catalog.py:70-71`, `catalog.py:893`).
- ABI handling beyond "phone ABI list, then universal".
- Package conflict detection between a project and an item before the project's first download (§9, known limitation 1).

### 0.5 Acceptance criteria (each is mechanical)

Commands use the test command of §1. `T_MOD <modules>` = the §1 module form.

1. Full suite: `T_PY 'test_*.py'` exits 0, last line `OK`, `Ran N tests` with N > 293.
2. Dead-model grep: `grep -rnE "dependency_app|dependency-apps|'dependency'|\"dependency\"|item_dependencies\(|_write_signer|signer\.sha256|_pull_dependency|_run_dependency" local/webui --include='*.py' --include='*.js' --include='*.html' --include='*.sql' --include='*.json'` prints lines only from `schema/003_catalog.sql`, `schema/004_device.sql`, `schema/005_follow.sql`, `tests/test_projects.py`.
3. Every test named in §10.1 (WP-1), §10.2 (WP-2), §10.3 (WP-3) exists with that exact `Class.method` name and passes.
4. `for f in local/webui/static/*.js; do node --check "$f" || echo FAIL; done` prints no `FAIL` (gate form: `docs/logs/2026-10-19-gate-r1.md:6`).
5. Static ownership grep: `grep -c "/projects.html" local/webui/static/{index,builds,item,schedules,devices,settings,projects}.html` prints a count ≥ 1 for each of the 7 files.
6. Live deploy (run once by the lead after the user approves §11 step 1; uses the live stack): after §11 steps 1–4, `PRAGMA user_version` is 5; `SELECT kind FROM upstream WHERE id=23` is `project`; after one pull of upstream 23, `DATA/upstream/23/<tag>/apks.json` exists and holds `"package": "app.revanced.android.gms"` and `"abi": "universal"`, and `project.signer_sha256` for project 1 is non-NULL.

### 0.6 Round budget

3 rounds. A round = all three packages delivered + the lead's gate run. Budget used up with criteria still failing → stop and report the failure trail; no fourth round without the user.

---

## 1. Test command (verified)

- Definition: `T_PY <pattern>` = `docker run --rm --network none -v "$PWD:/repo:ro" -e PYTHONPATH=/repo/local -e PYTHONDONTWRITEBYTECODE=1 relume-builder python3 -m unittest discover -s /repo/local/webui/tests -t /repo/local -p '<pattern>'`. Used as gate G1/H1 (`docs/logs/2026-10-19-gate-r1.md:5`, `docs/logs/2026-10-19-gate-r2.md:5`).
- Image: the `relume-builder` image does not exist on this machine today (`docker images` lists `relume-app:latest` and `relume-cfbypass`). The command is run with image **`relume-app`**, built from `local/Dockerfile` (python3 at `local/Dockerfile:6`). Verified 2026-10-23 at `4cc414a`: `T_PY 'test_*.py'` with `relume-app` → `Ran 293 tests in 53.902s`, `OK`, RC 0.
- Module form (verified the same day, `Ran 17 tests … OK` for two modules): `T_MOD <m…>` = `docker run --rm --network none -v "$PWD:/repo:ro" -w /repo/local -e PYTHONPATH=/repo/local -e PYTHONDONTWRITEBYTECODE=1 relume-app python3 -m unittest <m…>` with modules like `webui.tests.test_projects`. A single test: `webui.tests.test_projects.MigrationTest.test_v4_database_migrates`.
- Run from the repo root, foreground. Tests that need `setpriv` only pass in Linux, so always use the container.

---

## 2. Current state (verified facts the design depends on)

| Fact | Where |
|---|---|
| `upstream.kind` CHECK `('patches','cli','dependency','module')`; unique key repo+channel+tag | `schema/003_catalog.sql:4`, `:12` |
| `dependency_app(id, upstream_id, package, display_name)`; `item_dependency(item_id, dependency_app_id)` | `schema/003_catalog.sql:60-69` |
| `device_assignments` holds items only | `schema/004_device.sql:16-20` |
| `ota_jobs.item_ids` scopes a job | `schema/004_device.sql:24`, `ota.py:269-272` |
| `ota_installs.kind` CHECK `('item','dependency')`; dependency `ref_id` = upstream id | `schema/004_device.sql:32-33`, `ota.py:81`, `ota.py:170` |
| Migrations: numbered files, `executescript`, then `PRAGMA user_version=n` | `db.py:53-61` |
| Connections: autocommit, `foreign_keys=ON` | `db.py:31-36` |
| Startup runs `db.migrate()` then `seed_import()` | `__main__.py:28`, `__main__.py:32` |
| Seed imports only into an empty catalog; keys `dependency_apps`, `item_dependencies` | `catalog.py:912-923`, `catalog.py:889`, `catalog.py:937-938` |
| Seed upstream 23 `MorpheApp/MicroG-RE`, kind `dependency`, regex `^microg-[0-9.]+\.apk$`; dependency_app 1 → upstream 23 | `catalog-seed.json:3461-3468`, `catalog-seed.json:332-339` |
| Default schedule "Daily upstream" target `{"all": true}`; `all` = every upstream row | `catalog.py:893`, `catalog.py:68-71` |
| `upstream_row` reuses a row by repo+channel+tag, 400 "upstream kind conflict" on another kind | `catalog.py:370-380` |
| Release fetch with token, status mapping, release selection | `upstream.py:44-49`, `upstream.py:66-70`, `upstream.py:94-119` |
| Stable channel drops assets whose name contains `dev` | `upstream.py:80-81` |
| `_download` writes `<tag>.part`, checks size/digest, deletes part and fails on `OSError`/`ValueError`, then renames | `upstream.py:333-356` |
| `_write_signer` runs apksigner isolated on `apks[0]` only, after commit | `upstream.py:359-378`, `upstream.py:420-421` |
| Keep 2 newest tag dirs | `upstream.py:381-393` |
| `upstream.new_version` emitted once per new tag | `upstream.py:145-148` |
| `affected_items` joins `dependency_app` | `upstream.py:122-127` |
| Isolation helpers `jobs.isolate`, `jobs.runner_path` | `jobs.py:75-89` |
| APKEditor 1.4.9 at `/opt/APKEditor.jar`; build scripts use `RELUME_APKEDITOR` override | `local/Dockerfile:11`, `src/build/utils.sh:7` |
| Shell parser of APKEditor `info` output | `src/build/utils.sh:567-575` |
| Captured fixture: `package="app.morphe.android.youtube"`, `VersionCode="1561173354"`, `VersionName="21.20.405"` | `src/build/test-fixtures/apkeditor-info.txt:1-3` |
| Real run 2026-10-23 (`relume-app`, `java -jar /opt/APKEditor.jar info -i microg-7.2.1.apk -package -version-code -version-name`) printed the same layout: `package="app.revanced.android.gms"`, `VersionCode="255070218"`, `VersionName="7.2.1"`. The live MicroG APKs contain `lib/arm64-v8a`, `lib/armeabi-v7a`, `lib/x86`, `lib/x86_64` (→ `universal`) | live check, `local/data/upstream/23/7.2.1/` |
| Planner `plan(items, artifacts, deps_by_item, item_installs, dep_installs, present)`, D8 dependency rule | `ota.py:50-85` |
| Items use only the first phone ABI | `ota.py:18-20`, `build.py:233-242` |
| Pull-then-install for a never-downloaded dependency | `ota.py:191-220` |
| Item failure propagation `DEPENDENCY_FAILED` | `ota.py:223-225` |
| Device read model `dependencies` | `ota.py:97-117` |
| Assignment route items only, per-package uniqueness | `devices.py:396-413` |
| `NO_MATCHING_ABI` error code mapping | `devices.py:18` |
| Routes sorted by number of `{` placeholders, so fixed segments win over `{id}` | `server.py:278-281` |
| Path params that are digits become `int` | `server.py:161-162` |
| `MODULES` list | `server.py:16` |
| Events run synchronously in the emitting thread | `events.py:6-16` |
| Notifier: `EVENTS`, `DEFAULT_EVENTS` (all but `device.offline`), saved `events` list filters delivery, job `notify:false` suppresses | `notify.py:12-13`, `notify.py:49-57` |
| Settings page event checkboxes and hard-coded default list | `static/settings.html:26`, `static/settings.js:23`, `static/settings.js:32` |
| Job dedupe of identical queued jobs | `jobs.py:188-194` |
| Jobs list filters `kind`, `status`, target `item_id`/`device_id`; `target` parsed in JSON | `jobs.py:370-385`, `jobs.py:318` |
| Shell marks current nav link, renders worker/disk status | `static/app.js:175-196` |
| UI static tests: `NAV`, unsafe-HTML rule, page-id check pages `item`, `builds` | `tests/test_ui_static.py:6-7`, `:54` |
| Default notify events asserted in a test | `tests/test_device_notify.py:151` |
| Live DB today: `user_version` 4; one `dependency_app` (1 → 23, `app.revanced.android.gms`); 10 `item_dependency` rows; 0 devices; upstream 23 `downloaded_tag` `7.2.2`; 0 dependency `ota_installs` rows | read-only query 2026-10-23 |

---

## 3. Data model

### 3.1 Migration `schema/005_follow.sql` (frozen, verbatim)

`003`/`004` are not edited: the live DB is at version 4 and `db.migrate` never re-runs them (`db.py:56-59`). A fresh DB runs 001–005 and ends in the same schema. `executescript` commits any open transaction first, so `PRAGMA foreign_keys=OFF` before `BEGIN` takes effect; with foreign keys on, `DROP TABLE upstream` would cascade into `bundle_info` (`schema/003_catalog.sql:54`).

```sql
-- Follow project (spec 2026-10-23-follow-project-design §3.1). Owner: projects.
PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE upstream_new (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('patches','cli','project','module')),
  repo TEXT NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('stable','prerelease','tag')),
  tag TEXT,
  asset_regex TEXT,
  latest_tag TEXT, latest_published_at TEXT, checked_at TEXT, check_error TEXT,
  downloaded_tag TEXT, notified_tag TEXT);
-- dependency rows: downloaded_tag reset, so the next pull re-downloads and writes apks.json
INSERT INTO upstream_new(id, kind, repo, channel, tag, asset_regex, latest_tag, latest_published_at, checked_at,
                         check_error, downloaded_tag, notified_tag)
  SELECT id, CASE kind WHEN 'dependency' THEN 'project' ELSE kind END, repo, channel, tag, asset_regex,
         latest_tag, latest_published_at, checked_at, check_error,
         CASE kind WHEN 'dependency' THEN NULL ELSE downloaded_tag END, notified_tag
  FROM upstream;
DROP TABLE upstream;
ALTER TABLE upstream_new RENAME TO upstream;
CREATE UNIQUE INDEX upstream_key ON upstream(repo, channel, ifnull(tag, ''));

CREATE TABLE project (
  id INTEGER PRIMARY KEY,
  upstream_id INTEGER NOT NULL UNIQUE REFERENCES upstream(id),
  package TEXT UNIQUE,              -- read from the APKs by the first verified download; NULL before
  display_name TEXT,                -- NULL = label from the repo name
  auto_push INTEGER NOT NULL DEFAULT 0,
  signer_sha256 TEXT,               -- pinned signer (trust on first use); NULL until the first verified download
  signer_since TEXT,                -- downloaded tag when the pin was set
  signer_alert TEXT);               -- signer of downloaded_tag when it differs from the pin; NULL = no alert
INSERT INTO project(id, upstream_id, package, display_name)
  SELECT id, upstream_id, package, display_name FROM dependency_app;

CREATE TABLE item_dependency_new (
  item_id INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  PRIMARY KEY (item_id, project_id));
INSERT INTO item_dependency_new(item_id, project_id) SELECT item_id, dependency_app_id FROM item_dependency;
DROP TABLE item_dependency;
DROP TABLE dependency_app;
ALTER TABLE item_dependency_new RENAME TO item_dependency;

CREATE TABLE device_projects (
  device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  PRIMARY KEY (device_id, project_id));
-- user decision: phones that have an item depending on a project get the project assigned directly
INSERT OR IGNORE INTO device_projects(device_id, project_id)
  SELECT DISTINCT da.device_id, x.project_id
  FROM device_assignments da JOIN item_dependency x ON x.item_id = da.item_id;

CREATE TABLE ota_installs_new (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  device_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('item','project')),
  ref_id INTEGER NOT NULL,          -- item id, or project id
  build_job_id INTEGER,
  upstream_tag TEXT,
  package TEXT NOT NULL,
  version_name TEXT,
  version_code INTEGER,
  result TEXT NOT NULL CHECK (result IN ('installed','up_to_date','failed')),
  error_code TEXT,
  error_text TEXT);
INSERT INTO ota_installs_new(id, job_id, device_id, kind, ref_id, build_job_id, upstream_tag, package, version_name,
                             version_code, result, error_code, error_text)
  SELECT oi.id, oi.job_id, oi.device_id,
         CASE oi.kind WHEN 'dependency' THEN 'project' ELSE 'item' END,
         CASE oi.kind WHEN 'dependency'
              THEN COALESCE((SELECT p.id FROM project p WHERE p.upstream_id = oi.ref_id), oi.ref_id)
              ELSE oi.ref_id END,
         oi.build_job_id, oi.upstream_tag, oi.package,
         CASE oi.kind WHEN 'dependency' THEN COALESCE(oi.version_name, oi.upstream_tag) ELSE oi.version_name END,
         oi.version_code, oi.result, oi.error_code, oi.error_text
  FROM ota_installs oi;
DROP TABLE ota_installs;
ALTER TABLE ota_installs_new RENAME TO ota_installs;
CREATE INDEX ota_installs_lookup ON ota_installs(device_id, kind, ref_id, id DESC);

ALTER TABLE ota_jobs ADD COLUMN project_ids TEXT;   -- JSON list or NULL, like item_ids

COMMIT;
PRAGMA foreign_keys=ON;
```

Notes:
- MicroG keeps project id 1, so `item_dependency` pairs carry over unchanged (`catalog-seed.json:450-466`).
- On the live DB the `device_projects` insert adds 0 rows (0 devices paired today, §2). It is still required and tested.
- `upstream.downloaded_tag` of the MicroG row becomes NULL. The next pull downloads the current release again, verifies it and pins its signer. Until then the planner treats MicroG as "not downloaded" and pulls first (§5.4). Old `signer.sha256` files are ignored and removed by retention.
- `source.upstream_id`, `source.cli_upstream_id`, `bundle_info.upstream_id`, `project.upstream_id` refer to `upstream` by name and resolve to the rebuilt table.

### 3.2 Files on disk

Layout unchanged: `DATA/upstream/<upstream_id>/<safe tag>/` (`upstream.py:40-41`), 2 kept (`upstream.py:381-393`). For kind `project` the dir holds the matched APKs plus `apks.json`, written into the `.part` dir before commit (§5.2). `signer.sha256` is no longer written.

`apks.json` (frozen; list sorted by `file`):

```json
[{"file": "microg-7.2.1.apk", "abi": "universal", "package": "app.revanced.android.gms",
  "version_name": "7.2.1", "version_code": 255070218, "signer_sha256": "0b6c…43e7", "size": 12345678}]
```

`abi` is one of `catalog.ABIS` (`catalog.py:12`). `version_code` is an int or `null`. `signer_sha256` is lowercase hex.

### 3.3 Seed

- Upstream 23: `"kind": "project"` (was `dependency`, `catalog-seed.json:3465`).
- Key `dependency_apps` is replaced by `"projects": [{"auto_push": false, "display_name": "MicroG RE", "id": 1, "package": "app.revanced.android.gms", "upstream_id": 23}]`.
- `SEED_TABLES` (`catalog.py:881-890`): the `dependency_apps` entry becomes `"projects": ("project", ("id", "upstream_id", "package", "display_name", "auto_push"), "id")`. `BOOL_COLS` (`catalog.py:892`) gains `auto_push`. Signer columns are runtime state and not seeded.
- `item_dependencies` keeps its pairs; `seed_import` / `export_seed` use column `project_id` (`catalog.py:922-923`, `catalog.py:937-938`).
- `seed_text()` round trip stays byte-identical to the file (`tests/test_catalog_seed.py`).

---

## 4. Module `projects.py` (new; owned by WP-1)

### 4.1 Pure functions (frozen signatures)

```python
ABI_TOKENS = ("arm64-v8a", "armeabi-v7a", "x86_64", "aarch64", "armeabi", "arm64", "armv7", "x86")

def derive_regex(names: list[str]) -> str
def apk_abi(path: str | Path) -> str                      # raises ValueError
def parse_apk_info(text: str) -> dict | None              # {"package", "version_code", "version_name"}
def parse_signer(text: str) -> str | None                 # lowercase hex of "Signer #1 certificate SHA-256 digest"
def pick_apk(facts: list[dict], abis: list[str]) -> dict | None
def label(row: dict) -> str                               # display_name, else the repo part after "/"
```

- `derive_regex(names)`: for each name build one part, then return `"^(?:" + "|".join(sorted(set(parts))) + ")$"`. A part: the name is split at the ABI tokens (regex alternation in the order of `ABI_TOKENS`, longest first); each ABI token stays literal (`re.escape`). In the other segments each digit run `\d+(?:\.\d+)*` becomes the pattern `\d+(?:\.\d+)*` when the run contains a dot or the character before it is not a letter (or it starts the segment); otherwise it stays literal. All other text goes through `re.escape`. Verified examples:
  - `microg-0.3.9.250932.apk` → `^(?:microg\-\d+(?:\.\d+)*\.apk)$` (matches `microg-0.4.0.251001.apk`).
  - `mihon-v0.18.0-arm64-v8a.apk` + `mihon-v0.18.0.apk` → matches `mihon-v0.19.0-arm64-v8a.apk` and `mihon-v0.19.0.apk`, not `mihon-v0.19.0-x86.apk`.
  - `Seal-2.0.0-alpha.4-arm64-v8a-release.apk` → matches `…alpha.5-arm64-v8a-release.apk`, not `…-universal-release.apk`.
  - `termux-app_v0.118.2+github-debug_x86_64.apk` → matches `…v0.118.3…_x86_64.apk`, not `…_x86.apk`.
  - File names only shape the regex. They never decide the ABI (§0.2 item 8).
- `apk_abi(path)`: `zipfile.ZipFile(path).namelist()`, central directory only, nothing extracted. ABI dirs = `{n.split("/")[1] for n in names if n.startswith("lib/") and n.count("/") >= 2}`. Exactly one dir and it is in `catalog.ABIS` → that ABI; otherwise `universal`. `zipfile.BadZipFile` or `OSError` → `ValueError("not an APK: <file name>")`.
- `parse_apk_info(text)`: Python port of `src/build/utils.sh:568-576`. Remove `\r` and `"`. `package` = the first line matching `(?i)^\s*package`; `version_code` = first line matching `(?i)version[-_ ]?code`; `version_name` = first line matching `(?i)version[-_ ]?name`; each value = the text after the first `=` or `:`, stripped. `version_code` → `int` when all digits, else `None`. Returns `None` when `package` is missing or does not match `server.PACKAGE` (`server.py:28`).
- `parse_signer(text)`: same regex as `upstream.py:366` (`^Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)$` per stripped line), lowercased; `None` when absent.
- `pick_apk(facts, abis)`: facts sorted by `file`. For each ABI in `abis` (phone order), return the first fact with that `abi`. Then return the first fact with `abi == "universal"`. Else `None`.

### 4.2 Store functions consumed by WP-2 (frozen)

WP-2 calls these through `importlib.import_module('webui.projects')` at call time, like `ota.py:10-15`.

```python
def item_projects(item_id: int) -> list[int]                 # item_dependency, ordered by project id
def assigned_projects(device_id: int) -> list[int]           # device_projects, ordered by project id
def project_info(project_id: int, abis: list[str]) -> dict | None
def check_assignment(item_ids: list[int], project_ids: list[int]) -> None   # raises server.HttpError(400, msg)
```

`project_info` returns `None` for an unknown id, else exactly these keys:

| key | value |
|---|---|
| `project_id`, `upstream_id` | ids |
| `package` | `project.package` (may be `None` before the first download) |
| `display_name` | `label(row)` |
| `tag` | `upstream.downloaded_tag` when that dir has a readable `apks.json`, else `None` |
| `path`, `file`, `abi`, `version_name`, `version_code` | from `pick_apk(apks.json, abis)`; `path` absolute; all `None` when `tag` is `None` or no pick |
| `signer_alert` | `project.signer_alert` |
| `auto_push` | `bool` |

`check_assignment(item_ids, project_ids)` raises `HttpError(400, …)` with: `unknown item N`, `unknown project N`, or `two apps with package P` (packages of the items plus the known packages of the projects must be unique). It replaces the loop at `devices.py:402-409`.

### 4.3 Release verification and signer pinning (WP-1 internal, called from `upstream.py`)

```python
APKSIGNER = ["apksigner"]                                                     # moved from upstream.py:308
APKEDITOR = ["java", "-jar", os.environ.get("RELUME_APKEDITOR", "/opt/APKEditor.jar")]

def verify_release(upstream_id: int, part: Path, log) -> list[dict]          # raises ValueError
def after_download(upstream_id: int, tag: str, log, job_id: int | None) -> None
def enqueue_auto_push(project_id: int, notify: bool = True) -> list[int]
```

`verify_release` (runs on the `.part` dir, before commit):
1. `apks = sorted(part.glob("*.apk"))`; none → `ValueError("no APK in release")`.
2. Per APK: `abi = apk_abi(apk)`; then in a fresh temp dir (`chmod 0o770`), with `env=dict(upstream._child_env(), HOME=tmp)`, `cwd=tmp`, `timeout=120`, through `log.run(jobs.isolate(argv), …, on_line=collect)` — the same pattern as `upstream.py:370-374`:
   - `[*APKSIGNER, "verify", "--print-certs", jobs.runner_path(apk)]` → `parse_signer`; `None` or non-zero rc → `ValueError("signer unreadable: <file>")`.
   - `[*APKEDITOR, "info", "-i", jobs.runner_path(apk), "-package", "-version-code", "-version-name"]` → `parse_apk_info`; `None` or non-zero rc → `ValueError("package unreadable: <file>")`.
   - `jobs.JobFailed("cancelled")` from `log.run` is re-raised; any other `jobs.JobFailed` becomes `ValueError(str(e))`.
3. More than one distinct signer → `ValueError("signers differ within release")`. More than one distinct package → `ValueError("packages differ within release")`.
4. Project row by `upstream_id`: `package` set and different → `ValueError("package changed: <old> -> <new>")`. Another project has that package → `ValueError("package <p> already followed by project <id>")`.
5. Two facts with the same `abi` → one log line `[!] 2 APKs for <abi>: <names>; installs use <first by name>` (not a failure).
6. Write `part / "apks.json"` (§3.2) and return the facts.

Because `verify` runs inside `_download`'s `try` (§5.2), a `ValueError` deletes the part dir, sets `check_error = "download failed: …"` and fails the job (`upstream.py:348-351`). A bad release never becomes `downloaded_tag`; the previous one stays installable.

`after_download(upstream_id, tag, log, job_id)` (after commit, new tag only):
- Reads `apks.json` of `tag`; `sig` = the common signer, `pkg` = the common package.
- One `db.tx()`: set `project.package = pkg` when NULL. Then: pin NULL → `signer_sha256 = sig`, `signer_since = tag`, `signer_alert = NULL`. `sig == pin` → `signer_alert = NULL`. `sig != pin` and `sig != signer_alert` → `signer_alert = sig` and, after the transaction, `events.emit("project.signer_changed", {"project_id", "upstream_id", "tag", "old_sha256": pin, "new_sha256": sig, "job_id": job_id})` and log `[!] signer changed`. `sig != pin` and `sig == signer_alert` → nothing (no second event).
- `signer_alert` NULL after this step and `auto_push = 1` → `enqueue_auto_push(project_id, notify)` where `notify = jobs.get(job_id)["options"].get("notify", True)` when `job_id` is set (same read as `notify.py:53-56`), else `True`.

`enqueue_auto_push(project_id, notify)`: for each device in `device_projects` of the project, ordered by device id, `jobs.enqueue("ota", {"device_id": d, "project_ids": [project_id]}, "upstream", options={"notify": notify})`. Trigger `upstream` exists (`schema/001_core.sql:7`). Returns the job ids. Identical queued jobs collapse (`jobs.py:191-194`).

---

## 5. Flows

### 5.1 Add a project

1. UI → `POST /api/projects/assets {repo, channel}`. The server builds the in-memory row `{"kind": "project", "repo", "channel", "tag": None, "asset_regex": r"\.apk$"}` and calls `upstream._fetch(row)` unchanged (`upstream.py:94-119`): token reuse, stable/prerelease selection, 401/403/429/404 mapping.
2. User ticks assets → UI posts again with `selected: [names]`; server returns `asset_regex = derive_regex(selected)` and `matched` per asset. User edits the regex → UI posts `asset_regex`; server re-marks. `matched = bool(re.search(asset_regex, name))`, the same test as `match_assets` (`upstream.py:74-78`). JavaScript never evaluates the regex.
3. `POST /api/projects` validates, re-fetches the latest release with the final regex (must match ≥ 1 APK asset), creates the upstream row with `catalog.upstream_row(c, "project", repo, channel, None, asset_regex)` (`catalog.py:370-380`), inserts the `project` row and the `device_projects` rows, then enqueues `jobs.enqueue("upstream", {"upstream_id": uid}, "manual", options={"rebuild": False})` (same call as `catalog.py:527`).

### 5.2 Check and download

- Scheduled: an `upstream` schedule fires one job per upstream id; `all` includes project rows (`catalog.py:70-71`); `pull:false` only checks (`upstream.py:451-468`). No scheduler change.
- Manual: existing `POST /api/upstreams/{id}/pull` (`upstream.py:476-481`) with the project's `upstream_id`.
- New version: `_check` emits `upstream.new_version` (`upstream.py:145-148`). `affected_items` (`upstream.py:122-127`) joins `item_dependency x JOIN project p ON p.id = x.project_id WHERE p.upstream_id = ?`.
- `upstream.py` changes (WP-1):
  - `DEFAULT_REGEX` key `dependency` → `project` (`upstream.py:15`).
  - `match_assets`: the stable `dev` skip applies only when `row["kind"] != "project"` (`upstream.py:80-81`).
  - `_download(row, tag, assets, log, verify=None)`: when `verify` is given, call `verify(part)` inside the existing `try`, after the download loop and before leaving it (`upstream.py:338-351`).
  - `_fetch_row`: for kind `project`, `verify = lambda part: projects.verify_release(upstream_id, part, log)`; after `_download` returns, call `projects.after_download(upstream_id, row["latest_tag"], log, job_id)`, then `_prune`. The `_write_signer` call (`upstream.py:420-421`), `_write_signer` itself (`upstream.py:359-378`) and `APKSIGNER` (`upstream.py:308`) are deleted. `upstream.py` imports `projects` inside the function (avoids an import cycle; `projects.py` imports `upstream` at module level).

### 5.3 Signer change

- Effect of `signer_alert` set: every planner step for that project is `action='error', reason='SIGNER_CHANGED'` (§5.4); items that need it fail `DEPENDENCY_FAILED`; auto-push is not enqueued; `POST /api/projects/{id}/install` answers 409. Older tag dirs stay on disk, but only `downloaded_tag` is offered.
- Confirm: `POST /api/projects/{id}/signer/accept {"signer_sha256": <current alert>}` → `signer_sha256 = signer_alert`, `signer_since = downloaded_tag`, `signer_alert = NULL`; when `auto_push = 1`, `enqueue_auto_push(id)`.

### 5.4 OTA integration (WP-2)

Planner (pure), frozen signature:

```python
def plan(items, artifacts, deps_by_item, projects, item_installs, proj_installs, present) -> list[dict]
# deps_by_item: {item_id: [pinfo]}   projects: [pinfo] directly assigned and in scope
# proj_installs: {project_id: {"upstream_tag", "at"}}   pinfo: projects.project_info(...) dict
```

Project step rule `_project_action(p, prev, has)`, first match wins:
1. `p["signer_alert"]` → `('error', 'SIGNER_CHANGED')`
2. `p["tag"] is None` → `('install', 'not downloaded')` (path `None`; the handler pulls first)
3. `p["path"] is None` → `('error', 'NO_MATCHING_ABI')`
4. `not has(p["package"])` → `('install', 'missing on phone')`
5. `prev is None or prev["upstream_tag"] != p["tag"]` → `('install', 'new release')`
6. else → `('up_to_date', 'same release installed')`

Ordering and dedupe (the `planned` set exists, `ota.py:54`, `ota.py:71-72`):
- Items in catalog order as today. An item's `needs` = `[p["project_id"] for p in deps]`.
- Before an item whose action is `install`, each dependency not yet planned gets a step when its action is not `up_to_date` (today's D8 behaviour, `ota.py:69-83`).
- After all items, each directly assigned project not yet planned gets a step with any action, `up_to_date` included.
- Step dict: `{"kind": "project", "ref_id": project_id, "upstream_id", "package", "path", "file", "abi", "tag", "version_name", "version_code", "action", "reason"}`.

Other OTA changes:
- `abis_of(dev)` returns the full `abi_list` as a list (empty → `["universal"]`); `first_abi` stays for items (`ota.py:18-20`). `project_info` gets `abis_of(dev)`.
- `last_installs` returns `(items, projects)`; the second dict is keyed by `ref_id` for `kind='project'` rows (`ota.py:35-47`).
- `_record` (`ota.py:166-174`): project steps write `upstream_tag = step["tag"]`, `version_name`, `version_code` from the step, `package = step["package"] or ""`.
- `_run_project(job, dev, step, abis, log, failed)` replaces `_run_dependency` (`ota.py:209-220`):
  - `action == 'error'` → record `failed`, `error_code = step["reason"]`; add to `failed`.
  - `action == 'up_to_date'` → record `up_to_date`.
  - `action == 'install'` and `path` is `None` → `_pull_project` (replaces `ota.py:191-206`: enqueue `upstream` for `step["upstream_id"]` with `job.trigger` and `options={'rebuild': False, 'notify': …}`, wait as today, then `project_info(pid, abis)`). Still no tag → `NOT_DOWNLOADED`; `signer_alert` now set → `SIGNER_CHANGED`; no path → `NO_MATCHING_ABI`. Each records `failed` and adds to `failed`.
  - Before `adb install`, re-resolve `project_info` once more (file may be pruned), like `ota.py:230-239`.
- `_recovery_steps` (`ota.py:246-259`): assigned items, then directly assigned projects, then dependency projects of assigned items, matched by package; a project match builds the step from `project_info` with action `install` (or `error`, `NO_MATCHING_ABI` when `path` is `None`).
- Job scope: target `{device_id, item_ids?, project_ids?}`. Neither key → all assigned items and projects (as today, `ota.py:269`). Either key → the listed ids; a missing key means none of that kind. `ota_jobs.project_ids` stores the list (`ota.py:270-272`). `detail()` adds `project_ids` (`ota.py:311-318`).
- `on_build_succeeded` unchanged (`ota.py:321-326`): `item_ids` only, so dependency projects still install with the item.
- Scheduled OTA: `ota` schedules expand per device (`scheduler.py:25`); the plan includes projects. No scheduler change.

### 5.5 Device assignment

`PUT /api/devices/{id}/assignments {item_ids, project_ids?}` (`devices.py:396-413`). Omitted `project_ids` leaves `device_projects` rows untouched. Validation through `projects.check_assignment`. Item and project rows are replaced in one transaction. `POST/PUT /api/projects` with `device_ids` call `check_assignment(<device's item ids>, <device's project ids ∪ {id}>)` per device.

### 5.6 Edit and delete

- `PUT /api/projects/{id}` with a changed `channel`: updates `upstream.channel` in place and sets `latest_tag`, `downloaded_tag`, `notified_tag` to NULL; `sqlite3.IntegrityError` on `upstream_key` → 409 `project exists`. (B moved the project to another upstream row through `upstream_row`, which leaves an orphan row that `all` schedules keep checking, B risk 5; project upstream rows have no other references, so in place is safe.)
- Changed `asset_regex` or `channel` → `downloaded_tag = NULL`, then enqueue a pull, so the new asset set is fetched even when the tag is unchanged (`upstream.py:417-418` would skip it otherwise).
- Signer pin survives edits.
- `DELETE /api/projects/{id}`: 409 `project is a dependency of items` while `item_dependency` rows exist (mockup disables Unfollow, §8 row 26); 409 `pull queued or running` while an `upstream` job for that upstream id is `queued`/`running`. Otherwise one transaction deletes `device_projects` (cascade), the `project` row, then the `upstream` row; then `shutil.rmtree(DATA/upstream/<upstream_id>, ignore_errors=True)`. OTA history stays (no FK, `schema/004_device.sql:1`).

---

## 6. API (frozen shapes)

All mutating bodies are JSON objects (`server.py:186-191`); errors use `{"error": msg}` (`server.py:133-134`). Never answer 401 for GitHub token problems: `api()` would redirect to login (`static/app.js:22-25`).

Project object `P`:

```json
{"id": 1, "upstream_id": 23, "repo": "MorpheApp/MicroG-RE", "channel": "stable",
 "asset_regex": "^microg-[0-9.]+\\.apk$", "display_name": "MicroG RE", "label": "MicroG RE",
 "package": "app.revanced.android.gms", "auto_push": false,
 "latest_tag": "7.2.2", "latest_published_at": "2026-10-18T10:00:00Z", "downloaded_tag": "7.2.2",
 "checked_at": "2026-10-23T05:00:00Z", "check_error": null,
 "signer_sha256": "0b6c…43e7", "signer_since": "7.2.2", "signer_alert": null,
 "versions": [{"tag": "7.2.2", "current": true,
               "files": [{"name": "microg-7.2.2.apk", "abi": "universal", "size": 12345678,
                          "version_name": "7.2.2", "version_code": 255070300}]}],
 "disk_bytes": 24690000, "item_ids": [1, 3, 4, 6], "device_ids": [1]}
```

- `versions`: non-`.part` dirs under `DATA/upstream/<upstream_id>/`, the `downloaded_tag` dir first (`current: true`), then by mtime, newest first. `tag` = dir name. `files` from `apks.json`; a dir without `apks.json` lists its `*.apk` with `abi`, `version_name`, `version_code` = `null`.
- `disk_bytes`: sum of file sizes under `DATA/upstream/<upstream_id>/`.

| Method and path | Owner | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/projects` | projects.py | — | `{projects: [P]}` ordered by id | — |
| `POST /api/projects/assets` | projects.py | `{repo, channel: "stable"\|"prerelease", selected?: [str], asset_regex?: str}` | `{tag, published_at, asset_regex: str\|null, assets: [{name, size, matched}]}` (APK assets only) | 400 `invalid repo` / `invalid channel` / `unknown asset NAME` / `invalid asset_regex` / `no APK asset in latest release` / GitHub message (`not found`, `no release`, `http N`, `network error…`); 502 `token rejected`; 429 `rate limited…` |
| `POST /api/projects` | projects.py | `{repo, channel, asset_regex, display_name?: str\|null, auto_push?: bool, device_ids?: [int]}` | 201 `{id, job_id}` | 400 validation, `asset_regex matches no asset`, `upstream kind conflict`, `unknown device N`, `two apps with package P`; 409 `project exists`; GitHub errors as above |
| `PUT /api/projects/{id}` | projects.py | any of `{display_name, asset_regex, channel, auto_push, device_ids}` | 200 `P` | 404 `project not found`; 400 validation / `unknown device N` / `two apps with package P`; 409 `project exists` |
| `DELETE /api/projects/{id}` | projects.py | — | `{ok: true}` | 404; 409 `project is a dependency of items`; 409 `pull queued or running` |
| `POST /api/projects/{id}/signer/accept` | projects.py | `{signer_sha256}` | 200 `P` | 404; 409 `no signer change`; 409 `signer alert changed, reload` |
| `POST /api/projects/{id}/install` | ota.py | `{device_ids?: [int]}` (default: devices in `device_projects`) | `{job_ids: [int]}` | 404 `project not found`; 409 `signer changed: confirm on the Followed projects page`; 400 `project not assigned to any phone`; 400 `device not assigned` |
| `POST /api/upstreams/{id}/pull` | upstream.py (exists) | `{rebuild: false}` | `{job_id}` | unchanged |
| `PUT /api/devices/{id}/assignments` | devices.py | `{item_ids: [int], project_ids?: [int]}` | `{device}` | 400 `item_ids must be a list of integers` / `project_ids must be a list of integers` / `unknown item N` / `unknown project N` / `two apps with package P` |
| `POST /api/devices/{id}/ota` | ota.py | `{item_ids?, project_ids?}` (each a non-empty int list when present) | `{job_id}` | adds 400 `project_ids must be a non-empty list of integers`, 400 `project not assigned to this device` |
| `GET /api/devices` | ota.py | — | each device: existing fields + `assignments` (unchanged, `ota.py:103-108`) + `projects: [DP]`; `dependencies` removed | — |
| `GET /api/items/{id}`, `PUT /api/items/{id}` | catalog.py | `project_ids: [int]` replaces `dependency_app_ids` (`catalog.py:486-490`, `catalog.py:552-567`) | item detail with `project_ids` | 400 `invalid project_ids`, 400 `unknown project` |
| `GET /api/jobs?kind=ota` | jobs.py (exists) | — | `detail.installs[].kind` is `item`\|`project`; `detail.project_ids` added | — |

`DP` (per device, per project that is assigned or required by an assigned item):

```json
{"project_id": 1, "display_name": "MicroG RE", "package": "app.revanced.android.gms",
 "assigned": true, "required_by": [1, 3], "installed_tag": "7.2.1", "available_tag": "7.2.2",
 "available_file": "microg-7.2.2.apk", "available_abi": "universal",
 "update_available": true, "pending": true, "signer_alert": null}
```

- `installed_tag` from `last_installs`; `available_*` from `project_info(pid, abis_of(dev))`.
- `update_available` (user decision 5) = `installed_tag` and `available_tag` both set and `catalog.version_key(available_tag) > catalog.version_key(installed_tag)` (`catalog.py:29-31`).
- `pending` = the full plan (present unknown) has an `install` step for that project.

Validation:
- `repo` → `server.validate_repo` (`server.py:74-77`). `display_name` → `catalog._display_name` (`catalog.py:366-367`). `asset_regex` → string, ≤ 300 characters, compiles with `re`. `channel` → `stable`|`prerelease` only. `auto_push` → bool. `device_ids` → list of distinct ints, every id in `devices`. `repo` and `package` cannot change through PUT (400 `repo cannot change`).

---

## 7. Notifications

- `notify.EVENTS` (`notify.py:12`) gains `project.signer_changed`. `DEFAULT_EVENTS` (`notify.py:13`) therefore includes it (it keeps excluding only `device.offline`).
- `format_message` (`notify.py:78-96`) gains: `project.signer_changed` → `Signer changed: <label> <tag>. Installs are blocked until you trust the new signer.`, path `/projects.html`; and for `upstream.new_version` when a `project` row has that `upstream_id` → `New version: <label> <tag>`, path `/projects.html` (sources keep today's text).
- The event carries `job_id`, so a job with `notify: false` suppresses it (`notify.py:53-56`).
- Settings UI: a checkbox `value="project.signer_changed"` labelled "Project signer changed" in the "Notify on" fieldset (`static/settings.html:26`); the hard-coded default list (`static/settings.js:23`) gains `project.signer_changed`.
- Saved lists: delivery checks the saved `events` list (`notify.py:51`). A user who saved notification settings before this change does not get the event until they tick it and save (§11 step 5).

---

## 8. Frontend (mockup option A → API mapping)

Rules: existing Workbench shell and components (`static/app.css`); rendering only through the `html` template (`tests/test_ui_static.py:6`); no `data-toast` attributes and no mockup file names (`tests/test_ui_static.py:28-33`); helpers from `static/app.js` (`api`, `act`, `html`, `toast`, `every`, `fmt`, `shell`, `toggleTheme`). Page refresh: `every(15000, …)` while no dialog is open, like `static/devices.js:176`. Data loads: `GET /api/projects`, `GET /api/devices`, `GET /api/items`, `GET /api/schedules`, `GET /api/jobs?kind=upstream&status=running`, `GET /api/jobs?kind=upstream&status=queued`, `GET /api/jobs?kind=ota&limit=100`.

Attention rule (used by rows 9, 13, 17): `signer_alert` set, or `check_error` set, or any `DP` of this project with `update_available`.

| # | Mockup element | Source / action |
|---|---|---|
| 1 | Nav link "Followed projects", second position (after App list) | `href="/projects.html"` on all 7 pages; `shell()` marks it (`static/app.js:175-180`) |
| 2 | Sidebar "Worker busy · 1 download / Disk … · arm64" | existing `[data-shell-status]` box (`static/app.js:183-194`) |
| 3 | Sidebar user + sign out | existing shell markup (`data-logout`, `tests/test_ui_static.py:48`) |
| 4 | Crumb "Home", h1 "Followed projects" | static |
| 5 | "Check all now" | `POST /api/upstreams/{upstream_id}/pull {rebuild:false}` per project; toast "Checking N projects" |
| 6 | "Follow project" | opens add dialog (rows 38–50) |
| 7 | Theme toggle | `toggleTheme()` (`static/app.js:131`) |
| 8 | "6 projects" | `projects.length` |
| 9 | "4 need attention" | count by attention rule |
| 10 | "Last check … · next … via schedule …" | max `P.checked_at`; first enabled `GET /api/schedules` entry with `kind = "upstream"` and `target.all = true` → `next_run_at`, `name`; none → "no schedule checks projects" |
| 11 | "Disk 412 MB · 2 versions kept each" | sum `P.disk_bytes`; "2 versions kept" static (retention `upstream.py:381-393`) |
| 12 | Filter box | client filter on `label` + `repo` |
| 13 | All / Needs attention / Dependencies | client: attention rule; `item_ids.length > 0` |
| 14 | Project cell: icon, name, repo, "pre-release" chip, "dependency" chip | initials of `label`; `label`; `repo`; `channel == "prerelease"`; `item_ids.length > 0` |
| 15 | Latest release + date | `latest_tag`, `fmt` of `latest_published_at` |
| 16 | On disk (highlight when behind latest) | `downloaded_tag` or "none yet"; class `newer` when `!= latest_tag` |
| 17 | Status chips | "Signer changed" (`signer_alert`); "Auto-push paused" (`auto_push && signer_alert`); "Check error" (`check_error`); "Downloading" (an `upstream` job running for `upstream_id`); "Queued" (one queued); "Update available" (any `DP.update_available`); "No phones" (`device_ids` and `item_ids` empty); "Up to date" (none of the above and `downloaded_tag == latest_tag`) |
| 18 | Phone circles (amber = behind, faded = offline) | devices whose `projects[]` has this `project_id`: initials of `name`; amber when `DP.update_available`; faded when `!online` |
| 19 | Auto-push switch | `PUT /api/projects/{id} {auto_push}` |
| 20 | Footnote "↑/↓ moves the selection…" | static |
| 21 | ↑/↓ row selection | client |
| 22 | Pane header: icon, name, `github.com/<repo>` | `label`, `repo` (text, no link to an external URL: `tests/test_ui_static.py:31`) |
| 23 | Pane chips + channel badge | row 17 chips; `channel` |
| 24 | "Install to phone" menu: phones, online dot, `abi → file`, offline disabled, blocked text, empty text | devices with `DP.assigned`; `online`; `DP.available_abi → DP.available_file` ("no match" when null); disabled when `!online`; "Blocked: confirm the new signer first." when `signer_alert`; empty text when none; click → `POST /api/projects/{id}/install {device_ids:[d]}` |
| 25 | "Check now" | `POST /api/upstreams/{upstream_id}/pull {rebuild:false}` |
| 26 | ⋯ menu: "Edit assets, regex & channel", "Assign phones", "Unfollow" (disabled "used by N items") | edit dialog = add dialog in edit mode → `PUT /api/projects/{id} {asset_regex, channel, display_name}`; assign dialog (device checkboxes from `GET /api/devices`) → `PUT /api/projects/{id} {device_ids}`; confirm → `DELETE /api/projects/{id}`; disabled when `item_ids.length > 0` |
| 27 | Check-error alert + inline regex + "Save & check" | `check_error`; input prefilled with `asset_regex`; `PUT /api/projects/{id} {asset_regex}` (server enqueues the pull, §5.6) |
| 28 | Warn note "X is still on vA. Auto-push is off…" | client text when some `DP.assigned && DP.update_available` and `!auto_push` |
| 29 | Signer block: badge "Trusted since …", digest, trust-on-first-use text | `signer_since`; `signer_sha256` shown in colon-separated byte pairs; static text; "Recorded after the first download" when `signer_sha256` is null |
| 30 | Blocked alert, both digests, "Review & confirm signer…" | `signer_alert`; `signer_sha256` / `signer_alert`; opens signer dialog (row 51) |
| 31 | "Discard vX" | **dropped**: the next check downloads the same release again and raises the same alert; "Keep blocked" is the equivalent state |
| 32 | Kept versions: tag, date, badge Current / Previous · rollback / Quarantined, regex line, files with ABI and size | `P.versions`; date only for the version whose tag equals `latest_tag` (`latest_published_at`; per-release dates of older tags are not stored); badges "Current" (`current`), "Quarantined" (`current && signer_alert`), "Previous" (no rollback action exists, so the word "rollback" is dropped); `asset_regex`; `files[].name/abi/size` |
| 33 | Download progress bar "99.3 / 172 MB" | **dropped**: no byte progress is tracked; row 17 "Downloading" chip replaces it |
| 34 | Assigned phones list: dot, name, `abi → file`, behind/current | devices with `DP.assigned`; `online`; `DP.available_abi → DP.available_file`; "behind" when `DP.update_available`, "current" when `installed_tag == available_tag`, "not installed" when `installed_tag` is null |
| 35 | "Used as dependency by" list | `item_ids` → labels from `GET /api/items` |
| 36 | OTA history table (time, phone, trigger, version, result) | `GET /api/jobs?kind=ota&limit=100`, rows of `detail.installs` with `kind == "project" && ref_id == id`: job `finished_at`, device name, job `trigger`, `upstream_tag`, result "Installed" / "Current" (`up_to_date`) / "Blocked" (`error_code == "SIGNER_CHANGED"`) / "Failed"; job outcome `skipped_offline` → "Skipped". Limit: last 100 OTA jobs |
| 37 | Pane close, drawer on narrow screens, Escape | client + CSS |
| 38 | Add dialog step indicator 1/2/3 | client |
| 39 | "GitHub repository URL" + "Fetch release" | client parses `https://github.com/<owner>/<repo>` (or `owner/repo`) → `POST /api/projects/assets {repo, channel:"stable"}`; error message in row 49 |
| 40 | "Latest release vX · stable · Published … · N APK assets" | response `tag`, channel, `published_at`, `assets.length` |
| 41 | "Tick all 'freenet' assets" | **dropped**: flavor comes from file-name parsing; names only shape the regex |
| 42 | Assets table: checkbox, file, ABI, Flavor, Size | checkbox, `name`, `size`; **ABI and Flavor columns dropped**: ABI is read from the APK's `lib/` entries after download (user decision 8), so it is unknown here |
| 43 | Regex field "derived from your ticks, editable" + edit-confirm | tick change → `POST /api/projects/assets {repo, channel, selected}`; typing → same route with `asset_regex` (300 ms debounce); rows marked by `matched`; the existing "Replace your edited regex?" confirm stays client-side |
| 44 | Hint "Matches N of M assets · universal fallback …" | count of `matched`; the universal-fallback part is **dropped** (ABI unknown before download) |
| 45 | Info alert about ABI pick | static text: "Relume reads each APK's CPU ABI after download and installs the one for the phone's ABI list, else the universal one. Future releases must match this regex, or the check reports an error." |
| 46 | Channel Stable / Pre-release | change → re-post row 43 with the new `channel` (`prerelease`) |
| 47 | Auto-push switch | `auto_push` |
| 48 | "Assign to phones" checkboxes, online state, per-phone "gets" preview | `GET /api/devices` names + `online` → `device_ids`; per-phone preview **dropped** (ABI unknown before download) |
| 49 | Status line in dialog footer | client messages and API errors |
| 50 | "Follow project" save | `POST /api/projects {repo, channel, asset_regex, auto_push, device_ids}` → select the new row |
| 51 | Signer dialog: name, old/new digest, version, ack checkbox, "Keep blocked", "Trust new signer" | `label`, `signer_sha256`, `signer_alert`, `downloaded_tag`; button enabled by the checkbox; `POST /api/projects/{id}/signer/accept {signer_sha256: signer_alert}` |
| 52 | Toast | existing `#toast` + `toast()` |

Other page changes (not in the mockup):

| File | Change |
|---|---|
| `static/devices.js` | Render `d.projects` instead of `d.dependencies` (`static/devices.js:29`): label, `installed_tag → available_tag`, "assigned" or "required by …", pending mark, "Blocked: signer changed" badge linking `/projects.html`. `pending` count (`static/devices.js:23`, shown at `:31`) includes `projects[].pending`. Assignment dialog (`static/devices.js:92-101`) also lists `GET /api/projects` and sends `project_ids`. History shows `i.version_name \|\| i.upstream_tag` (`static/devices.js:63`). |
| `static/devices.html` | Assignment dialog content split into `#as-items` and `#as-projects` (`static/devices.html:38-40`). |
| `static/item.js`, `static/item.html` | Dependency picker loads `GET /api/projects` and sends `project_ids` (`static/item.js:403-432`); label "Dependency apps" → "Dependencies" (`static/item.html:70`). |
| `static/schedules.js` | "All sources" → "All sources and projects" (`static/schedules.js:19`, `:55`). |
| `static/settings.html`, `static/settings.js` | §7 checkbox and default list. |

---

## 9. Errors and known limitations

| Situation | Behaviour |
|---|---|
| GitHub 401 / 403 / 429 / 404 / no release in `POST /api/projects/assets` or `POST /api/projects` | 502 `token rejected`; 429 `rate limited…`; 400 with the message (`upstream.py:94-116`) |
| Same failures during a scheduled check | existing: `check_error` set (`upstream.py:137-142`), shown on the row and in the App list banner (`static/index.js:76-81`) |
| Regex invalid / matches nothing at create | 400 `invalid asset_regex` / 400 `asset_regex matches no asset` |
| Later release matches nothing | `check_error` `no matching asset` (`upstream.py:115-116`); row 27 fixes it |
| Unreadable signer or package, mixed signers, mixed packages, package changed, package already followed, not a zip | `verify_release` `ValueError` → nothing committed, `check_error = download failed: …`, job failed; previous tag stays current |
| Two APKs with the same ABI | log line; `pick_apk` takes the first by file name |
| Size/digest mismatch | existing (`upstream.py:343-351`) |
| Signer differs from pin | not a failure: download commits, `signer_alert` set, `project.signer_changed` emitted once, every install path blocked (§5.3) |
| No APK for the phone's ABIs and no universal | step `failed`, `NO_MATCHING_ABI` |
| Not downloaded at OTA time | pull then install; still missing → `NOT_DOWNLOADED` (dependency items → `DEPENDENCY_FAILED`) |
| Android rejects the update (different signer on the phone) | existing `SIGNATURE_MISMATCH` + "Uninstall & retry" (`devices.py:15`, `ota.py:344-357`) |

New error codes for spec device §5: `SIGNER_CHANGED`, `NOT_DOWNLOADED`.

Known limitations (accepted):
1. Before a project's first download its package is unknown, so `check_assignment` cannot catch an item and a project with the same package on one phone. After the download the next assignment save catches it; the planner does not.
2. The OTA history in the pane covers the last 100 OTA jobs (row 36).
3. `apk_abi` reads the zip central directory in the server process (no extraction, no code execution); apksigner and APKEditor run as the isolated runner uid.

---

## 10. Work packages (3, disjoint file ownership)

No file appears in two packages. No package touches `jobs.py`, `scheduler.py`, `events.py`, `db.py`, `build.py`, `ci.py`, `auth.py`, `settings.py` (the Python module). WP-1 commits `schema/005_follow.sql` and the `projects.py` §4.2 functions first; WP-2 mocks them through module seams until then (pattern `ota.py:10-15`, `tests/test_device_ota.py:52`). The lead merges WP-1 and WP-2 together (WP-1 deletes `catalog.item_dependencies`, WP-2 deletes its only caller `ota.py:10-11`) and runs the full suite on the combined tree.

### 10.1 WP-1 — Project store and release pipeline

Files (only these):
- `local/webui/schema/005_follow.sql` (new), `local/webui/projects.py` (new), `local/webui/upstream.py`, `local/webui/catalog.py`, `local/webui/catalog-seed.json`, `local/webui/notify.py`, `local/webui/server.py` (only `MODULES` at `server.py:16`: add `'projects'` after `'upstream'`)
- `local/webui/tests/test_projects.py` (new), `local/webui/tests/test_catalog_store.py`, `local/webui/tests/test_catalog_seed.py`, `local/webui/tests/test_catalog_upstream.py`, `local/webui/tests/test_device_notify.py`, `local/webui/tests/catalog_rows.py`, `local/webui/tests/fixtures/catalog/fake-apkeditor.sh` (new), `local/webui/tests/fixtures/catalog/fake-apksigner.sh`
- `docs/superpowers/specs/2026-10-19-webui-design.md` (replace the dependency-app rows with the new shapes or a pointer to this file)

Delivers: §3 (migration, seed), §4 (all of `projects.py`), §5.1–5.3, §5.6, §6 routes owned by `projects.py`, catalog `project_ids`, deletions in §12 for its files, §7.

Frozen contracts it provides: §3.1 SQL; §3.2 `apks.json`; §4.2 functions; §6 `P`; auto-push job `("ota", {"device_id", "project_ids": [id]}, "upstream", options={"notify": bool})`; event `project.signer_changed` payload §4.3.

Fixture rules: `fake-apkeditor.sh` prints the captured layout of `src/build/test-fixtures/apkeditor-info.txt` with package `app.revanced.android.gms`, version name `7.2.1`, version code `255070218`, and package `com.example.other` when its `-i` path contains `other`. `fake-apksigner.sh` keeps today's `ABAB…` digest and prints `CDCD…` (64 hex) when its last argument contains `resigned`. Downloads in tests write real tiny zip files (built with `zipfile`) so `apk_abi` works.

Tests (exact names; all must pass):
- `test_projects.MigrationTest`: `test_v4_database_migrates` (DB at version 4 via 001–004; insert dependency upstream 23 with `downloaded_tag`, `dependency_app` 1, `bundle_info` row on a patches upstream, item + `item_dependency (1,1)`, a device with that item assigned, `ota_installs` dependency row `ref_id=23`; migrate; assert kind `project`, `downloaded_tag` NULL, `project` row 1 with package, link kept, `device_projects (device, 1)`, install row `kind='project', ref_id=1`, `bundle_info` kept, `PRAGMA foreign_key_check` empty, `ota_jobs.project_ids` exists, `user_version` 5), `test_fresh_database_tables` (no `dependency_app`; `project`, `device_projects` exist).
- `test_projects.PureTest`: `test_derive_regex` (the 4 examples of §4.1), `test_apk_abi_from_lib_entries` (no `lib/` → universal; one ABI → it; two ABIs → universal; non-zip → `ValueError`), `test_pick_apk` (phone order, second phone ABI, universal fallback, `None`), `test_apk_info_captured_fixture` (reads `src/build/test-fixtures/apkeditor-info.txt` → `{"package": "app.morphe.android.youtube", "version_code": 1561173354, "version_name": "21.20.405"}`), `test_parse_signer`.
- `test_projects.AssetsRouteTest`: `test_lists_apk_assets_and_derives_regex`, `test_regex_edit_marks_matches`, `test_unknown_selected_asset_400`, `test_invalid_regex_400`, `test_token_rejected_502`, `test_rate_limited_429`.
- `test_projects.CrudRouteTest`: `test_create_enqueues_pull`, `test_create_with_devices`, `test_duplicate_409`, `test_kind_conflict_400`, `test_regex_matches_nothing_400`, `test_put_regex_resets_download_and_pulls`, `test_put_channel_in_place`, `test_put_devices_package_conflict_400`, `test_delete_removes_rows_and_dir`, `test_delete_with_items_409`, `test_delete_with_active_pull_409`, `test_list_shape`.
- `test_projects.SignerRouteTest`: `test_accept_matching_digest`, `test_accept_stale_digest_409`, `test_accept_without_alert_409`, `test_accept_enqueues_auto_push`.
- `test_projects.ReleaseTest`: `test_verify_writes_apks_json_and_pins`, `test_package_filled_on_first_download`, `test_mixed_signers_not_committed`, `test_mixed_packages_not_committed`, `test_package_change_not_committed`, `test_unreadable_info_not_committed`, `test_signer_change_sets_alert_and_emits_event_once`, `test_same_signer_clears_alert`, `test_auto_push_enqueues_for_assigned_devices_only`, `test_no_auto_push_with_alert`, `test_tools_run_isolated` (with `RELUME_RUNNER_UID`, both tool runs start with `setpriv --reuid=…`, like `tests/test_catalog_upstream.py:219-229`), `test_check_assignment_rules`.
- `test_catalog_upstream.PullTest` (existing class, `tests/test_catalog_upstream.py:119`): `test_stable_dev_filter_skips_project_rows`, `test_affected_items_through_projects`; the two dependency signer tests (`tests/test_catalog_upstream.py:205-229`) are deleted.
- `test_catalog_store.CrudTest.test_item_project_ids` replaces `test_item_dependencies` (`tests/test_catalog_store.py:188-203`): get/put `project_ids`, `unknown project` 400; table-name and delete-cascade tests use `project` (`tests/test_catalog_store.py:22`, `:33-44`, `:167-172`).
- `test_catalog_seed`: round trip passes with the `projects` key.
- `test_device_notify.NotifyTest`: `test_default_events_include_signer_changed` (replaces the list at `tests/test_device_notify.py:151`), `test_format_signer_changed`, `test_format_project_new_version`.

Test command: `T_MOD webui.tests.test_projects webui.tests.test_catalog_store webui.tests.test_catalog_seed webui.tests.test_catalog_upstream webui.tests.test_device_notify`, then `T_PY 'test_*.py'`.

### 10.2 WP-2 — Install path (OTA and devices)

Files (only these):
- `local/webui/ota.py`, `local/webui/devices.py`
- `local/webui/tests/test_device_ota.py`, `local/webui/tests/test_device_otajob.py`, `local/webui/tests/test_device_routes.py`, `local/webui/tests/device_helpers.py`

Delivers: §5.4, §5.5, `POST /api/projects/{id}/install`, `project_ids` on `POST /api/devices/{id}/ota` and on assignments, `GET /api/devices` `projects: [DP]`, `detail.project_ids`, deletions in §12 for its files.

Frozen contracts it consumes: §3.1 tables `project`, `device_projects`, `ota_installs`, `ota_jobs.project_ids`; §4.2 functions; auto-push job target. It provides: §5.4 `plan` signature and step dict; §6 `DP`; install route.

Tests (exact names; all must pass):
- `test_device_ota.PlanTest` (existing class, `tests/test_device_ota.py:18`; its current tests move to the new signature): `test_dependency_project_before_item`, `test_direct_project_install_and_up_to_date`, `test_project_missing_on_phone`, `test_assigned_and_dependency_planned_once`, `test_signer_alert_error_step`, `test_no_matching_abi_error_step`, `test_not_downloaded_install_without_path`, `test_scope_project_ids_only`.
- `test_device_ota.PlanTest.test_projects_shape_and_update_available` (replaces the `dependencies` assertion at `tests/test_device_ota.py:87`) (update true when downloaded tag is newer; false when equal; false when installed tag is null).
- `test_device_otajob.OtaJobTest` (existing class, `tests/test_device_otajob.py:19`): `test_project_install_records_tag_and_version`, `test_signer_blocked_dependency_fails_item`, `test_missing_project_pulled_then_installed`, `test_project_still_missing_not_downloaded`, `test_install_route_default_devices`, `test_install_route_409_on_alert`, `test_install_route_unassigned_400`, `test_recovery_finds_project_package`, `test_ota_now_project_ids`, `test_auto_push_target_runs_project_only`.
- `test_device_routes.DeviceRoutesTest` (existing class, `tests/test_device_routes.py:13`): `test_assignments_with_project_ids`, `test_assignments_unknown_project_400`, `test_assignments_package_conflict_400`, `test_assignments_without_project_ids_keeps_rows`.

Test command: `T_MOD webui.tests.test_device_ota webui.tests.test_device_otajob webui.tests.test_device_routes`, then `T_PY 'test_*.py'` on the merged tree.

### 10.3 WP-3 — Frontend

Files (only these):
- `local/webui/static/projects.html` (new), `local/webui/static/projects.js` (new), `local/webui/static/index.html`, `builds.html`, `item.html`, `schedules.html`, `devices.html`, `settings.html`, `devices.js`, `item.js`, `schedules.js`, `settings.js`, `app.css` (only styles the mockup needs and the file lacks)
- `local/webui/tests/test_ui_static.py`

Delivers: §8 (all rows), §7 settings UI, nav link on every page.

Frozen contracts it consumes: §6 (all shapes, including `P`, `DP`, `detail.installs[].kind`), §7 event name.

Tests (exact names; all must pass):
- `NAV` (`tests/test_ui_static.py:7`) gains `href="/projects.html"`; `StaticRulesTest.test_pages_have_nav_and_logout` passes for all pages including `projects.html`.
- `PageIdsTest.test_every_id_a_page_script_looks_up_exists_in_its_html` page list (`tests/test_ui_static.py:54`) becomes `('item', 'builds', 'projects', 'devices')`.
- `StaticRulesTest.test_projects_page_uses_project_api` (new): `projects.js` contains `'/api/projects'`, `/api/projects/assets`, `/signer/accept`, `/install`, `/api/upstreams/`, `'/api/devices'`, `kind=ota`.
- `StaticRulesTest.test_settings_lists_signer_event` (new): `settings.html` contains `value="project.signer_changed"`; `settings.js` contains `project.signer_changed`.
- `StaticRulesTest.test_no_dead_dependency_model_leftovers` (new): no file in `static/` contains `dependency-apps`, `dependency_app`, or `d.dependencies`.
- `StaticServeTest.test_every_page_and_asset` passes with the new page.
- Criterion 0.5-4 (`node --check`) passes.

Test command: `T_MOD webui.tests.test_ui_static`, then the `node --check` loop of 0.5-4.

---

## 11. Deploy steps (live stack, in order)

Run from the repo root. The stack is compose project `relume`; the live DB is `local/data/webui.db` (WAL mode: `-wal` and `-shm` files exist), mounted at `/private/data` (`local/docker-compose.yml:86-87`, `local/.env:12`).

1. Stop the web app so nothing writes: `docker compose -p relume -f local/docker-compose.yml stop app`.
2. Back up the DB with SQLite's online backup (copies WAL content consistently): `python3 -c "import sqlite3; s=sqlite3.connect('local/data/webui.db'); d=sqlite3.connect('local/data/webui.db.bak-2026-10-23'); s.backup(d); d.close(); s.close()"`. Check: `python3 -c "import sqlite3; c=sqlite3.connect('file:local/data/webui.db.bak-2026-10-23?mode=ro', uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0], c.execute('PRAGMA user_version').fetchone()[0])"` prints `ok 4`.
3. Start the app with the new code: `docker compose -p relume -f local/docker-compose.yml start app`. Startup runs migration 005 (`__main__.py:28`).
4. Check: `PRAGMA user_version` = 5 (read-only query as in step 2 on `local/data/webui.db`); then press "Check now" on MicroG RE (or `POST /api/upstreams/23/pull`) and confirm criterion 0.5-6.
5. Notifications: users who saved notification settings before this release open Settings → Notifications, tick "Project signer changed", and save (§7).
6. Rollback: stop the app, `mv local/data/webui.db local/data/webui.db.failed`, remove `local/data/webui.db-wal` and `local/data/webui.db-shm`, `cp local/data/webui.db.bak-2026-10-23 local/data/webui.db`, check out the previous code, start the app.

---

## 12. Deleted (no traces)

- Table `dependency_app` (by migration 005), `item_dependency.dependency_app_id`.
- `catalog.list_dependency_apps` and route `GET /api/dependency-apps` (`catalog.py:606-609`, `catalog.py:965`); `catalog.item_dependencies` (`catalog.py:612-630`); `dependency_app_ids` in item detail/PUT (`catalog.py:486-490`, `catalog.py:552-567`); the `signer.sha256` filter (`catalog.py:781`); section comment "apps and dependency apps" (`catalog.py:583`) becomes "apps".
- `upstream.DEFAULT_REGEX["dependency"]` (`upstream.py:15`), `upstream.APKSIGNER` (`upstream.py:308`), `upstream._write_signer` (`upstream.py:359-378`) and its call (`upstream.py:420-421`).
- `ota._item_dependencies` (`ota.py:10-11`), `'dependency'` step kind (`ota.py:81`, `ota.py:256`, `ota.py:301`), `_pull_dependency` / `_run_dependency` (`ota.py:191-220`), `dependencies` in the device view (`ota.py:109-117`).
- `d.dependencies` rendering (`static/devices.js:29`), the dependency-app picker (`static/item.js:403-432`) and label (`static/item.html:70`).
- Tests of the removed behaviour (`tests/test_catalog_upstream.py:205-229`, `tests/test_catalog_store.py:188-203`, dependency fixtures `MICROG` in `tests/test_device_ota.py:8-9` and `tests/test_device_otajob.py:29-30` rewritten as project fixtures).

---

## 13. Spec author notes for the lead (contradictions and deviations)

1. Test image: `relume-builder` is absent on this machine; §1 uses `relume-app` (verified 293 tests OK at `4cc414a`).
2. Mockup add-dialog ABI column, Flavor column, "Tick all freenet" and per-phone preview are dropped: ABI comes from `lib/` entries after download (user decision 8).
3. Unfollow while items use the project returns 409 (mockup), not B's cascade.
4. Live DB has 0 paired devices, so the migration's direct MicroG assignment adds 0 rows there (still specified and tested).
5. Deviation from B: channel edit updates the upstream row in place (B moved rows and left orphans, B risk 5).
6. Migration nulls MicroG `downloaded_tag`; the first post-migration pull re-downloads and pins the then-current signer; old `signer.sha256` is not carried over.
7. Real APKEditor 1.4.9 `info` on the live MicroG APK printed the captured-fixture layout; that APK has 4 `lib/` ABIs → universal.
