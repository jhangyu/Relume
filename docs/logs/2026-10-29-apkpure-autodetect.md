# APKPure format auto-detect (2026-10-29)

Change: `get_apkpure` (src/build/utils.sh) downloads to `<name>.xapk` regardless of type, then detects the format from the zip listing: root `AndroidManifest.xml` = APK, else any `*.apk` entry = XAPK, else fail with expected vs found.
- apk/bundle: APK is renamed to `<name>.apk`, XAPK is merged with APKEditor to `<name>.apk`.
- bundle_extract: XAPK is renamed to `<name>.apkm` and extracted to `download/<name>/` (what build-item.sh expects: ext=apkm, dir `download/<name>`); a plain APK fails loudly.

Base commit a26e663 plus the working-tree change to src/build/utils.sh (committed in the following commit).

## Live check (relume-app container, modified utils.sh copied to /root/utils-new.sh, /repo untouched; work dir deleted)
Script `scripts/tmp/apkpure-auto.sh`, raw log `scripts/tmp/ap-auto.log` (gitignored). URL `https://apkpure.com/tw/jptt/com.joshua.jptt/download`. RC = in-script `RC=$?` right after `get_apkpure`.

| case | RC | decisive output |
|---|---|---|
| latest, type apk (XAPK served) | 0 | `Merge splits apk to standalone apk`, `probe.apk` 21017715 B, `unzip -Z1` lists `AndroidManifest.xml` |
| 3.2, type apk (APK served) | 0 | `File is a standalone APK, renaming`, `probe.apk` 17520197 B, lists `AndroidManifest.xml` |
| latest, bundle_extract | 0 | `probe.apkm` 34168614 B + dir `probe/` |
| 3.2, bundle_extract | 1 | `probe.xapk is a standalone APK but type=bundle_extract expects an XAPK bundle` |

## Linux gate
`docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell` -> `RC=0`, `total: 396 tests, skipped: 0, wall 7.9s`.

## Notes
- No test added (needs live network).
- Unknown-format message was not exercised live.
