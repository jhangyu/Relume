-- Stable per-phone id sent at sign-in (Android Settings.Secure.ANDROID_ID). A phone that signs in again lands on the
-- row with its id. Rows from before this column, and ADB rows, keep NULL.
ALTER TABLE devices ADD COLUMN device_uid TEXT;
CREATE UNIQUE INDEX devices_device_uid ON devices(device_uid);
