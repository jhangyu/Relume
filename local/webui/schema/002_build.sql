-- Build tables (spec build §3.3). Owner: build. Settings key artifact_retention (default 3).
CREATE TABLE build_jobs (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  fail_code TEXT, fail_detail TEXT,
  app_version TEXT, patches_version TEXT, cli_version TEXT);
CREATE INDEX build_jobs_item ON build_jobs(item_id, job_id DESC);
CREATE TABLE artifacts (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
  item_id INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  variant TEXT NOT NULL,
  abi TEXT NOT NULL CHECK (abi IN ('arm64-v8a','armeabi-v7a','x86','x86_64','universal')),
  package TEXT, version_name TEXT, version_code INTEGER, patches_version TEXT,
  signer_sha256 TEXT, path TEXT NOT NULL, size INTEGER, sha256 TEXT, created_at TEXT NOT NULL);
CREATE INDEX artifacts_item ON artifacts(item_id, abi, created_at DESC);
