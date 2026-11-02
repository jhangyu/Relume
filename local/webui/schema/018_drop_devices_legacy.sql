-- legacy is derived: kind = 'app' AND device_uid IS NULL (decision 1, devices.LEGACY_SQL); 013/014 stay as history.
ALTER TABLE devices DROP COLUMN legacy;
