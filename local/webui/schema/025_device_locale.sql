-- phone's first system locale as a BCP-47 tag (companion sync), used to request its language split from Google Play.
-- NULL = not reported yet; ADB rows read it live with getprop instead.
ALTER TABLE devices ADD COLUMN locale TEXT;
