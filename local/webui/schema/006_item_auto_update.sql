-- Per-app update switch and the appcheck job kind (spec 2026-10-25-per-app-update-build-design §2, §3.2).
PRAGMA foreign_keys=OFF;
BEGIN;

ALTER TABLE item ADD COLUMN auto_update INTEGER NOT NULL DEFAULT 0;

CREATE TABLE jobs_new (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('build','upstream','ota','appcheck')),
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

COMMIT;
PRAGMA foreign_keys=ON;
