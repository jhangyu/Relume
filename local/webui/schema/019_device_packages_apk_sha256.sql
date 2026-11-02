-- sha256 of the installed base APK as the phone (or adb) reported it; NULL = not reported (companion up to v1.4.0). Decision 15.
ALTER TABLE device_packages ADD COLUMN apk_sha256 TEXT;
