ALTER TABLE upstream ADD COLUMN download_error TEXT;   -- last download/verify failure of the latest release; check_error = last release check failure
UPDATE upstream SET download_error = substr(check_error, 18), check_error = NULL
  WHERE check_error LIKE 'download failed: %';          -- 17-char prefix written by the old upstream._download
UPDATE settings SET value = json_insert(value, '$.events[#]', 'project.assignment_dropped')
  WHERE key = 'notify' AND json_type(value, '$.events') = 'array';   -- saved notify configs get the new event (decision 18)
