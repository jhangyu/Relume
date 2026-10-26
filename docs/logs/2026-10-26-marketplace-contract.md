# Marketplace refactor — frozen convergence contract

Frozen by the lead 2026-10-26 from plan §0 at commit time; only the user may change it. Parking lot at the bottom.


**End state.** The spec's server, migration, web and Android changes are on main, the Python + shell suite passes on macOS and in the Linux gate within 90 s, the Android leg passes, migration 010 is verified on a backup copy of the live database, and the round-2 reviewer reports zero blockers; release, redeploy and the new companion release are then the user's steps.

**In scope.**
1. Server: migration 010, catalog layer, enabled-app model, build flow, update run, global schedule, APKMirror locator, HTTP API (spec §1–§6).
2. Removals and their tests and docs (spec §9, §10).
3. Web: Fluent 2 foundation, Marketplace, Apps, Schedules, restyled kept pages, light only (spec §7).
4. Android: MD3 fixed palette, three tabs, Marketplace, Apps with Install, App settings, Schedules, Settings behind the avatar (spec §8).
5. Live migration check on a `.backup` copy (spec §4, §11).

**Out of scope.** A second catalog (awesome-morphe); GitLab release download; cron schedules; `bundles.json` `featured[]`; release, Portainer redeploy, Clean up press, companion release (user, spec §11); on-device checks; any change to `android/tokens.md`, `android/m3.css` or other mockup files.

**Acceptance (mechanical; run on main after round 2 or the fix round).**
1. macOS suite: `python3 scripts/test.py --legs python,shell` → exit 0, `skipped: 0` per leg, wall time ≤ 90 s.
2. Linux gate: `docker build -f local/Dockerfile --target app -t relume-gate . && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell` → exit 0, wall time ≤ 90 s.
3. Android leg: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` → exit 0.
4. Every committed test in plan §3 exists and ran: `grep -n "def <name>"` / `grep -n "fun <name>"` → one hit each, and its id appears in the gate 1/3 output.
5. Deleted files are gone: `ls` of every path in plan §3 "Delete" → "No such file".
6. Removed-symbol greps (plan §4) → no hit (exit 1).
7. Live migration check (plan §5): the scratch check script exits 0 on a fresh `.backup` copy of `local/data/webui.db` at the merged HEAD; log with command, self-captured RC and HEAD hash in `docs/logs/2026-10-26-mp-migration-check.md`.
8. Boot smoke on the migrated copy (plan §5): `boot()` returns; `GET /api/marketplace?per_page=1` (session) → 200 with `total` ≥ 1 after one `POST /api/catalog/refresh`; `GET /api/apps` → 2 apps (YouTube, Reddit); `GET /api/schedule` → `spec.every == "day"`.
9. Pages render: the scratch render check (plan §8) passes for `/`, `/apps.html`, `/schedules.html`, `/builds.html`, `/devices.html`, `/projects.html`, `/settings.html` (headless Chrome, page has `<main`, no `Uncaught` or failed resource in the log; run by the reviewer, plan §6).
10. Round-2 reviewer (fresh, opus): 0 blockers on the commits of rounds 1–2.

**Round budget.** 2 implementation rounds + at most 1 fix round. Budget spent with acceptance open → stop and report the failure trail to the user.

## Parking lot

- Migration 010 drops `app.display_name` without copying it to `store_app`. Apps that are not in the catalog store (for example Tasker) then show their package name until a catalog or custom source names them. Source: impl-migration report, .claude/tmp/mp-1.1/report.md.
- Android DTOs guessed in f5e23ff (CustomSource, OtaSchedule, PatchView) must be checked against the real API in round 2.
