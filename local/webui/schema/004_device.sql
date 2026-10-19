-- device part (spec device §3.1). ota_* keep device_id without FK so OTA history outlives the device.
CREATE TABLE devices (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  serial TEXT NOT NULL UNIQUE,
  mdns_name TEXT,
  host TEXT NOT NULL,
  connect_port INTEGER NOT NULL,
  model TEXT,
  abi_list TEXT,
  sdk INTEGER,
  android_release TEXT,
  ota_after_build INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT
);
CREATE TABLE device_assignments (
  device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  PRIMARY KEY (device_id, item_id)
);
CREATE TABLE ota_jobs (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  device_id INTEGER NOT NULL,
  item_ids TEXT,
  mode TEXT NOT NULL DEFAULT 'normal' CHECK (mode IN ('normal','force_reinstall','downgrade')),
  outcome TEXT CHECK (outcome IN ('done','partial','failed','skipped_offline'))
);
CREATE TABLE ota_installs (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  device_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('item','dependency')),
  ref_id INTEGER NOT NULL,
  build_job_id INTEGER,
  upstream_tag TEXT,
  package TEXT NOT NULL,
  version_name TEXT,
  version_code INTEGER,
  result TEXT NOT NULL CHECK (result IN ('installed','up_to_date','failed')),
  error_code TEXT,
  error_text TEXT
);
CREATE INDEX ota_installs_lookup ON ota_installs(device_id, kind, ref_id, id DESC);
