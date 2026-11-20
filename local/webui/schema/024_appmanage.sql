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
