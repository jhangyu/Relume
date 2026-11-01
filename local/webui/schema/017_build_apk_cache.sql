-- APK cache files a build read or stored (webui/build.py cache_name): JSON list of names under DATA/apk-cache.
-- NULL for builds before this migration: clean-up then keeps none of their cache files (they download again once).
ALTER TABLE build_jobs ADD COLUMN apk_cache TEXT;
