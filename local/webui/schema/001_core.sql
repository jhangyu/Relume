CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- value = JSON
CREATE TABLE sessions (token TEXT PRIMARY KEY, expires_at TEXT NOT NULL);
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('build','upstream','ota')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','success','failed','cancelled')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','schedule','upstream','after_build')),
  schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
  target TEXT NOT NULL,          -- JSON, atomic target (see below)
  options TEXT NOT NULL DEFAULT '{}',  -- JSON, per-job flags (§0.3), normalized by jobs.enqueue (§5)
  reason TEXT,                   -- one-line failure/cancel reason
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE INDEX jobs_kind_id ON jobs(kind, id);
CREATE TABLE schedules (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('upstream','build','ota')),
  target TEXT NOT NULL,          -- JSON, may be a group/all target
  spec TEXT NOT NULL,            -- JSON, see §6
  options TEXT NOT NULL DEFAULT '{}',  -- JSON, kind-specific, e.g. upstream {"rebuild":true}
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT, next_run_at TEXT);
