# Marketplace final gate (lead, quiet host, no other agent running)

HEAD `374030da2fb5395725e58ba4fedd35315c9cc9c2`, working tree clean for local/ android/ src/ scripts/.

| Check | Command | RC | Decisive output |
|---|---|---|---|
| Linux gate | `docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell` | 0 | 397 tests, skipped 0, wall 7.5 s (no per-test cap trips) |
| Android leg | `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` | 0 | 42 tests, 0 failed, skipped 0 |
| Removed-symbol greps | the 10 greps of plan §4 | 1 each | no hit |

Contract status:
- Items 2–10 pass. Item 9 (render) and items 7 and 8 (migration and boot smoke) were last run in review pass 2 at 77d425a. The server code changed after that only in b22b1e3 and 9035770 (additive fields, covered by the Linux gate), and the live deployment 0.0.4 migrated and ran 19 update and 13 build jobs successfully.
- Item 11: the web front-end review passed in round 4 (docs/logs/2026-10-26-fe-review-web.md). The Android front-end review is partial: round 2 found two must-fix items (R2-1 chip row gap, fixed in 93b8752; R2-2 silent empty Marketplace, fixed in 374030d). The user stopped agent device testing and tests the Android UI on the device themselves. Items not re-checked on the device are listed in the "Round 2 (PARTIAL)" section of docs/logs/2026-10-26-fe-review-android.md.
