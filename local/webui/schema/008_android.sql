-- Android companion (spec 2026-10-25-android-companion-design §3). Owner: auth (devices token), ota (rest).
PRAGMA foreign_keys=OFF;
BEGIN;

-- app rows have no ADB address; kind and created_at defaults keep the existing devices.pair INSERT valid
CREATE TABLE devices_new (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'adb' CHECK (kind IN ('adb','app')),
  serial TEXT UNIQUE, mdns_name TEXT, host TEXT, connect_port INTEGER,      -- NULL for app rows
  token_hash TEXT UNIQUE,                                                    -- sha256 hex of the device token, NULL = no token
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  model TEXT, abi_list TEXT, sdk INTEGER, android_release TEXT,
  app_version_code INTEGER,                                                  -- companion version reported by the app
  ota_after_build INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT,
  CHECK (kind = 'app' OR (serial IS NOT NULL AND host IS NOT NULL AND connect_port IS NOT NULL)),
  CHECK (kind = 'adb' OR (serial IS NULL AND host IS NULL AND connect_port IS NULL AND ota_after_build = 0)));
INSERT INTO devices_new(id, name, kind, serial, mdns_name, host, connect_port, model, abi_list, sdk, android_release,
                        ota_after_build, last_seen_at)
  SELECT id, name, 'adb', serial, mdns_name, host, connect_port, model, abi_list, sdk, android_release,
         ota_after_build, last_seen_at
  FROM devices;
DROP TABLE devices;
ALTER TABLE devices_new RENAME TO devices;

-- the server's view of what is installed on a phone
CREATE TABLE device_packages (
  device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  package TEXT NOT NULL, version_code INTEGER NOT NULL, version_name TEXT, signer_sha256 TEXT,
  reported_at TEXT NOT NULL, PRIMARY KEY (device_id, package));

-- job_id NULL = install done by the app; the at default keeps ota._record valid
CREATE TABLE ota_installs_new (
  id INTEGER PRIMARY KEY,
  job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
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
  error_text TEXT,
  at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
INSERT INTO ota_installs_new(id, job_id, device_id, kind, ref_id, build_job_id, upstream_tag, package, version_name,
                             version_code, result, error_code, error_text, at)
  SELECT oi.id, oi.job_id, oi.device_id, oi.kind, oi.ref_id, oi.build_job_id, oi.upstream_tag, oi.package,
         oi.version_name, oi.version_code, oi.result, oi.error_code, oi.error_text,
         (SELECT COALESCE(j.finished_at, j.created_at) FROM jobs j WHERE j.id = oi.job_id)
  FROM ota_installs oi;
DROP TABLE ota_installs;
ALTER TABLE ota_installs_new RENAME TO ota_installs;
CREATE INDEX ota_installs_lookup ON ota_installs(device_id, kind, ref_id, id DESC);

-- followed projects re-download once, so verify_release writes the per-file sha256
UPDATE upstream SET downloaded_tag = NULL WHERE kind = 'project';

COMMIT;
PRAGMA foreign_keys=ON;
