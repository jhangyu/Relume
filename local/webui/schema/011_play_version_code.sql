-- Google Play versionName -> versionCode pairs proven by a downloaded APK (webui/playversion.py). The device is part
-- of the key because some apps publish one versionCode per ABI.
CREATE TABLE play_version_code (
  package TEXT NOT NULL, version_name TEXT NOT NULL, device TEXT NOT NULL, version_code INTEGER NOT NULL,
  verified_at TEXT NOT NULL, PRIMARY KEY (package, version_name, device));
