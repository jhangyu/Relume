-- APKMirror variant whose patch build passed the guard (webui/build.py). abis = the non-universal ABIs the download
-- serves, space-joined and sorted ('' = universal only): the same version needs another row after an ABI change.
CREATE TABLE IF NOT EXISTS apkm_variant (
  package TEXT NOT NULL, version_name TEXT NOT NULL, abis TEXT NOT NULL, version_code INTEGER NOT NULL,
  variant_url TEXT NOT NULL, bundle INTEGER NOT NULL, verified_at TEXT NOT NULL,
  PRIMARY KEY (package, version_name, abis));
