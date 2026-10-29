# APKPure fallback for apps without an APKMirror listing

Change: `apps._variants(r)` rewrites download kind `apkmirror` to `apkpure` when the app has no `apkmirror_list_url` and has `apkpure_url`. Used by `_missing_url`, `job_input` recipe variants and `inputs_sha`.

- Native macOS `python3 scripts/test.py --legs python,shell`: RC=1, shell leg fails on `realpath -e` (macOS, pre-existing, platform not supported by the shell leg), not run as gate.
- Linux gate: `docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell`
  RC=0, `total: 396 tests, skipped: 0, wall 7.2s` (python 374, shell 22; includes 2 new ApkpureFallbackTest tests).
- Gate image was the existing relume-gate (built 2026-10-28), a rebuild timed out at the 300 s cap. Image has no app code baked in (repo mounted at /repo).
