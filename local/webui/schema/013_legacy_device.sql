-- A companion up to v1.2.0 sends no device_uid, so it cannot be matched to a row on its next sign-in. Its row is
-- hidden (legacy = 1) and install-only until the same token syncs with a uid (then ota.sync converts or merges it).
ALTER TABLE devices ADD COLUMN legacy INTEGER NOT NULL DEFAULT 0;
