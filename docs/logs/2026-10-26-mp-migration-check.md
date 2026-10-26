# Marketplace migration 010 — live migration check (task 1.1)

Run 2026-10-26T05:26:08Z on main at `b5c3d4931df2af5abff5d644cd289be2704f008d` (schema 010 and `db.py` equal to HEAD: `git diff HEAD -- local/webui/schema/010_marketplace.sql local/webui/db.py` empty). Script `.claude/tmp/mp-migrate/check.py` (scratch, sha1 `9534cceed801857bb6e1758b0f79534886505c87`). The live file was only read through SQLite's online backup.

Commands (plan §5 step 4):

```bash
rm -rf .claude/tmp/mp-migrate/data && mkdir -p .claude/tmp/mp-migrate/data .claude/tmp/mp-migrate/key
sqlite3 local/data/webui.db ".backup .claude/tmp/mp-migrate/data/webui.db"
python3 .claude/tmp/mp-migrate/check.py .claude/tmp/mp-migrate/data > .claude/tmp/mp-migrate/check.log 2>&1; RC=$?; echo RC=$RC >> .claude/tmp/mp-migrate/check.log
```

Deviation from spec §4 "Expected result": the spec says "41 `app` rows with URLs and `apkm_state='manual'`". The former seed has 41 apps, of which 40 have a URL; `net.dinglisch.android.taskerm` has none. 010 applies spec §4 item 2 (`manual` only for rows with a list URL or APKPure URL) to the seed rows too, so that row stays `pending`. The check asserts that.

`check.py --boot` (plan §5 step 5) is written but not run here: it needs the round-1 routes of tasks 1.3/1.4; the round-1 gate runs it.

Output (`.claude/tmp/mp-migrate/check.log`):

```
ok starts at user_version 9
ok user_version 10
ok foreign_key_check empty
ok one built-in bundle on MorpheApp/morphe-patches stable
ok items 1 YouTube on, 2 Reddit on, 3 YT Music off, all on the built-in bundle, mode auto
ok variants, overrides and engine options unchanged
ok item_dependency (1,1), (3,1)
ok 2 device_assignments kept
ok 41 app rows
ok every app row with a URL is manual
ok rows without a URL stay pending (spec says 41 with URLs, the seed has 40)
ok app.display_name, icon_file, icon_fetched_at dropped
ok grp and source gone
ok catalog tables present
ok old update row found before the migration
ok one update schedule: old id 1, daily 09:00, enabled, next_run_at kept, options notify true
ok old spec is daily 09:00
ok OTA schedule rows unchanged
ok no appcheck job
ok 8 upstream rows (1, 2, 19, 20, 23, 24, 25, 26)
ok bundle_info only at downloaded_tag
ok build_jobs.inputs_sha added
ALL OK
RC=0
```
