-- App rows from before device_uid (012) belong to phones still running companion <= v1.2.0 with their old token:
-- install-only like every other legacy row until the phone syncs with a uid (ota.sync then converts the row back).
UPDATE devices SET legacy = 1 WHERE kind = 'app' AND device_uid IS NULL;
