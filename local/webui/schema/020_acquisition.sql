ALTER TABLE app RENAME COLUMN apkm_state TO apkm_state_old;
ALTER TABLE app ADD COLUMN apkm_state TEXT NOT NULL DEFAULT 'pending'
  CHECK (apkm_state IN ('pending','not_found','ambiguous','blocked','failed'));   -- lookup outcome while no list URL is set
ALTER TABLE app ADD COLUMN apkmirror_list_by TEXT CHECK (apkmirror_list_by IN ('lookup','user'));   -- who set apkmirror_list_url; NULL exactly when it is NULL (kept by apklocate.locate/put_download)
UPDATE app SET apkm_state = CASE WHEN apkm_state_old IN ('found','manual') THEN 'pending' ELSE apkm_state_old END,
  apkmirror_list_by = CASE WHEN apkmirror_list_url IS NULL THEN NULL WHEN apkm_state_old = 'found' THEN 'lookup' ELSE 'user' END;
UPDATE app SET apkm_retry_at = NULL WHERE apkm_state = 'blocked';
ALTER TABLE app DROP COLUMN apkm_state_old;
ALTER TABLE store_app ADD COLUMN icon_tried_at TEXT;   -- when icon_tried_url last failed (UTC ISO); retried after catalog.ICON_RETRY_AFTER
UPDATE store_app SET icon_tried_url = NULL;            -- old failures carry no time: each is tried once more
