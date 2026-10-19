-- Catalog tables (spec catalog §3, xposed §4). Owner: catalog.
CREATE TABLE upstream (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('patches','cli','dependency','module')),
  repo TEXT NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('stable','prerelease','tag')),
  tag TEXT,                       -- only for channel='tag'
  asset_regex TEXT,               -- NULL = default for the kind (upstream.DEFAULT_REGEX)
  latest_tag TEXT, latest_published_at TEXT, checked_at TEXT, check_error TEXT,
  downloaded_tag TEXT, notified_tag TEXT);
-- UNIQUE(repo, channel, tag) with NULL tags treated as equal
CREATE UNIQUE INDEX upstream_key ON upstream(repo, channel, ifnull(tag, ''));

CREATE TABLE source (
  id TEXT PRIMARY KEY,            -- slug, e.g. 'morphe'
  name TEXT NOT NULL,
  upstream_id INTEGER NOT NULL REFERENCES upstream(id),
  cli_upstream_id INTEGER NOT NULL REFERENCES upstream(id),
  engine TEXT NOT NULL DEFAULT 'morphe' CHECK (engine IN ('morphe','lspatch','npatch')),
  keystore TEXT NOT NULL DEFAULT 'morphe.keystore');

CREATE TABLE app (
  package TEXT PRIMARY KEY,
  display_name TEXT,              -- NULL until the icon fetcher reads Play og:title
  icon_file TEXT, icon_fetched_at TEXT,
  apkmirror_list_url TEXT, apkmirror_example_url TEXT, apkmirror_example_url2 TEXT, apkpure_url TEXT,
  apkmirror_versions TEXT,        -- JSON array, newest first; NULL = never seen
  apkmirror_checked_at TEXT);

CREATE TABLE grp (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  color TEXT CHECK (color IS NULL OR (length(color) = 7 AND substr(color, 1, 1) = '#')),
  position INTEGER NOT NULL);

CREATE TABLE item (
  id INTEGER PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source(id),
  package TEXT NOT NULL REFERENCES app(package),
  key TEXT NOT NULL,
  display_name TEXT,
  group_id INTEGER REFERENCES grp(id) ON DELETE SET NULL,   -- NULL = Ungrouped
  position INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  version_mode TEXT NOT NULL DEFAULT 'auto' CHECK (version_mode IN ('auto','pinned','latest')),
  version_pin TEXT,
  allow_nearest INTEGER NOT NULL DEFAULT 0,
  variants TEXT NOT NULL,                       -- JSON, build §3.1 variants[]
  patch_overrides TEXT NOT NULL DEFAULT '{}',   -- JSON, catalog §4
  engine_options TEXT,                          -- JSON, xposed §4
  UNIQUE (source_id, package));

CREATE TABLE bundle_info (
  upstream_id INTEGER NOT NULL REFERENCES upstream(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  package TEXT NOT NULL,
  patches TEXT NOT NULL,                        -- JSON, upstream.merge_bundle output
  PRIMARY KEY (upstream_id, tag, package));

CREATE TABLE dependency_app (
  id INTEGER PRIMARY KEY,
  upstream_id INTEGER NOT NULL REFERENCES upstream(id),
  package TEXT NOT NULL,
  display_name TEXT NOT NULL);

CREATE TABLE item_dependency (
  item_id INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  dependency_app_id INTEGER NOT NULL REFERENCES dependency_app(id) ON DELETE CASCADE,
  PRIMARY KEY (item_id, dependency_app_id));
