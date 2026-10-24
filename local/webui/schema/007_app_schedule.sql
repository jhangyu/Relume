-- App schedules (spec 2026-10-25-per-app-update-build-design §4.2): kinds upstream and build become app.
PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE schedules_new (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('app','ota')),
  target TEXT NOT NULL,
  spec TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT, next_run_at TEXT);

-- ids: item ids the old target expands to now (NULL = old target was "all" or an ota target)
INSERT INTO schedules_new(id, name, kind, target, spec, options, enabled, last_run_at, next_run_at)
WITH x AS (
  SELECT s.*,
    CASE
      WHEN kind = 'upstream' AND json_type(target, '$.source_ids') = 'array' THEN
        (SELECT json_group_array(id) FROM (SELECT id FROM item
           WHERE source_id IN (SELECT value FROM json_each(s.target, '$.source_ids')) ORDER BY id))
      WHEN kind = 'build' AND json_type(target, '$.group_ids') = 'array' THEN
        (SELECT json_group_array(id) FROM (SELECT id FROM item
           WHERE ifnull(group_id, 0) IN (SELECT value FROM json_each(s.target, '$.group_ids')) ORDER BY id))
      WHEN kind = 'build' AND json_type(target, '$.item_ids') = 'array' THEN json_extract(target, '$.item_ids')
    END AS ids,
    ifnull(json_extract(target, '$.all'), 0) AS is_all
  FROM schedules s)
SELECT id, name, CASE kind WHEN 'ota' THEN 'ota' ELSE 'app' END,
  CASE WHEN kind = 'ota' THEN target
       WHEN is_all = 1 OR ids IS NULL OR ids = '[]' THEN '{"all":true}'
       ELSE json_object('item_ids', json(ids)) END,
  spec,
  CASE WHEN kind = 'ota' THEN options ELSE
    json_object('build', json(CASE WHEN kind = 'upstream' AND ifnull(json_extract(options, '$.rebuild'), 1) = 0 THEN 'false' ELSE 'true' END),
                'force', json(CASE WHEN kind = 'build' AND ifnull(json_extract(options, '$.only_changed'), 0) = 0 THEN 'true' ELSE 'false' END),
                'notify', json(CASE WHEN ifnull(json_extract(options, '$.notify'), 1) = 0 THEN 'false' ELSE 'true' END),
                'update', json(CASE WHEN kind = 'upstream' THEN 'true' ELSE 'false' END)) END,
  CASE WHEN kind != 'ota' AND is_all != 1 AND (ids IS NULL OR ids = '[]') THEN 0 ELSE enabled END,
  last_run_at,
  CASE WHEN kind != 'ota' AND is_all != 1 AND (ids IS NULL OR ids = '[]') THEN NULL ELSE next_run_at END
FROM x;

DROP TABLE schedules;
ALTER TABLE schedules_new RENAME TO schedules;

COMMIT;
PRAGMA foreign_keys=ON;
