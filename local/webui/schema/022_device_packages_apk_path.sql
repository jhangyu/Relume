-- base APK path the ADB hash (apk_sha256) was read from; the hash is reused while the path is unchanged
-- (Android installs every APK into a new random directory). NULL = not read over adb (Relume app rows).
ALTER TABLE device_packages ADD COLUMN apk_path TEXT;
