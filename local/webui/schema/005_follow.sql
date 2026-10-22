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
