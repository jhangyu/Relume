-- upstream.notified_tag always equalled latest_tag after a check (webui/upstream.py _check): the new-version
-- event now compares the checked tag with the row's previous latest_tag.
ALTER TABLE upstream DROP COLUMN notified_tag;
-- bundle.list_error keeps only list read failures; the entries the last good read skipped move to list_skipped
-- (JSON list of error texts, NULL when none). A stored 'skipped: ...' text becomes a one-entry list, so the
-- API text joined from it is unchanged.
ALTER TABLE bundle ADD COLUMN list_skipped TEXT;
UPDATE bundle SET list_skipped = json_array(substr(list_error, 10)), list_error = NULL
  WHERE substr(list_error, 1, 9) = 'skipped: ';
