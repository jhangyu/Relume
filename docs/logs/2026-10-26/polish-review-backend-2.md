# Backend review: edc406d (project icon route) and 1064a41 (db.tx)

Verdict: merge-ready. No blocker, no should-fix.

## Checked OK
- Auth: route not in auth.PUBLIC / SESSION_ONLY (auth.py:17-21); server.py:216-219 requires session or device token, same as download_apk.
- Path traversal: zip entry name only read inside the APK; output name fixed `icon.<ext>`, ext limited to png|webp|jpg by `_APK_ICON` (projects.py:22, :559).
- Zip bomb: entry picked only if declared file_size <= 256 KiB (projects.py:552); CPython ZipExtFile stops at file_size and checks CRC. Bad zip raises -> logged 500 (server.py:187).
- Isolation: APKEditor via existing `_tool` -> `jobs.run_isolated` (setpriv runner uid, scrubbed env, temp HOME, 120 s timeout; projects.py:545, jobs.py:104-120); stdin DEVNULL, list argv, no shell. Tag dir is server-written (default umask), runner cannot plant files there.
- Cache/concurrency: once per tag dir; racing first requests each mkstemp + os.replace (atomic); glob `icon.*` cannot match `.icon*` temp files (projects.py:560-563, :574). New tag or re-pull replaces the dir.
- Content type from suffix (server.py:305); check.log shows image/png and image/webp.
- Errors: tool failure -> 500 with reason; no raster icon -> logged + 404 by design. findall never returns None.
- db.tx (db.py:66-73): original exception always re-raised; ROLLBACK skipped only when SQLite already ended the transaction (in_transaction accurate with isolation_level=None); a failing ROLLBACK still raises with the original chained.
- Evidence: gate logs 391 tests, 0 skipped, RC=0 (polish-icon, debug-sqlite, polish-web-install); `git diff HEAD -- local/webui` empty. Nothing rerun, no gap.

## Nits
1. projects.py:546-547: tool failure -> 500, not cached; an APK APKEditor cannot read reruns the JVM (1-2 s, up to 120 s) on every tile load. Fix: write the icon.none marker on failure too, or accept (the follow step already parsed this APK with the same tool).
2. projects.py:560-563: if f.write fails (disk full), the `.iconXXXX` temp file stays in the tag dir. Fix: try/unlink tmp/re-raise.
3. projects.py:560 vs upstream.py:344-345: re-pull of the same tag during its first icon extraction can create a file after `rmtree(final, ignore_errors=True)`, so `part.rename(final)` fails "Directory not empty" outside the try and that pull job fails; next pull recovers. Fix: none now, or rename final aside before rmtree.

## Parking lot (no severity)
- Untrusted icon bytes served with extension-based type and no `X-Content-Type-Options: nosniff` (catalog icons too); one header in server._file covers both.
- db.py:73: a failing COMMIT can leave the thread-local connection inside a transaction (pre-existing).
- Icon is served while a signer alert blocks the APK download (409); harmless, differs from download route.
