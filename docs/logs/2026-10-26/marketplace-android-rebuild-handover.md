# Marketplace refactor, Android rebuild — Session Handover

> **Created:** 2026-10-26 15:40 (Asia/Taipei)
> **Purpose of the handover:** the next session rebuilds the Android app with the last two fixes and gets it onto the user's phone, and onto the Relume releases if the user approves. The end state is that the user tests the build from HEAD on the device.
> **Current status:** in progress. The code is done and verified; the rebuild and install are not done.
> **Trusted version anchor:** branch `main`; HEAD `f2a462f`. The Android code under test is `374030d`; f2a462f only adds logs on top of it.

## 0. Quick read (60 seconds)

- **Goal:** the user's phone runs the Relume Android app built from HEAD, so the user can test the Marketplace refactor UI on the device.
- **Current state:** phone `10AFCF269C007FB` (V2507A) has the debug build from `3bfcf65`. Two later fixes are not on it:
  - `93b8752`: wrapped Schedules interval chips sit 8 dp apart instead of 24.6 dp.
  - `374030d`: a failed first Marketplace load shows an error banner with Retry instead of a silent empty list.
- **Where things stand:** all code is committed, the final gate is green, and the team is closed (no live members).
- **Next action:** build the debug APK at HEAD and install it on `10AFCF269C007FB`. The commands are in §1.
- **Biggest risks and red lines:**
  - Use only phone `10AFCF269C007FB`; the user chose it. Do not touch `cc5bf709`.
  - Do not change device settings. `svc power stayon` was never approved.
  - Do not push, and do not create the `companion-v*` tag, without the user's explicit OK. There are 82 unpushed commits on main.
- **The user has ruled:** every parking-lot item in the contract is unimportant and will NOT be done. Do not start any of them.

## 1. Start-up sequence

1. Read this file, and `docs/logs/2026-10-26-mp-final-gate.md` for the final gate evidence.
2. Run `git rev-parse --short HEAD; git status --porcelain -- local android src scripts | wc -l; adb devices`.
   - Expect `f2a462f` (or newer, if the user committed), `0`, and `10AFCF269C007FB device`.
3. Check that the phone is unlocked: `adb -s 10AFCF269C007FB shell dumpsys window | grep -i keyguard`.
   - If `isKeyguardShowing=true`, ask the user to unlock it. Installs fail while it is locked, with `INSTALL_FAILED_ABORTED: User rejected permissions`.
4. Build: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:assembleDebug --console=plain --no-watch-fs > .claude/tmp/mp-final/assemble.log 2>&1; echo RC=$?`
   - Expect `RC=0`. The output is `android/app/build/outputs/apk/debug/relume-debug.apk`.
5. Install: `adb -s 10AFCF269C007FB install -r android/app/build/outputs/apk/debug/relume-debug.apk; echo RC=$?`
   - Expect `Success`. The phone may ask for a USB-install confirmation; the user must tap Allow.
6. Tell the user the build is installed and which commit it is.

## 2. Goal, current state and cause

### Goal
The user tests the Android UI on the device. The user stopped agent-driven device testing because it was too slow.

### Current state
- The installed build is `3bfcf65`, which lacks `93b8752` and `374030d`.
- The live server is Portainer stack `relume`, image `jhangyu/relume-app:latest`, version `0.0.4`, port 65427 on the Mac (192.168.26.8). Migration 010 ran successfully.
  - Evidence: `curl -s localhost:65427/api/health` and the read-only DB query in §12.

### Cause
None. This is a deployment step, not a bug.

## 3. Scope and version-control state

- **In scope:**
  - Build the debug APK and install it on `10AFCF269C007FB`.
  - Optionally, if the user asks: a companion release.
- **Out of scope:**
  - All parking-lot items. The user ruled them unimportant, so do not do them.
  - Any further agent UI testing on the device.
- **Branch / HEAD:** `main` / `f2a462f`.
- **Working tree:** clean for local/, android/, src/ and scripts/. Many old untracked files under `docs/logs/` predate this work; they are not this session's, so leave them.
- **Key commits:**
  - `243e56c` to `7ebf805`: requirements, design, plan and frozen contract.
  - `2cea775` to `3968a1d`: round 1 (migration 010, catalog layer, apps model, APKMirror locator plus API, Android foundation).
  - `1f8bea1` to `d002ea9`: round 2 (web pages, Android screens).
  - `4928572`, `7256fa3`, `b22b1e3`, `9035770`: review fixes and API fields.
  - `43f845f` to `8d7928b`: web mockup fixes (the web front-end review PASSED).
  - `e0ce77a` to `374030d`: Android mockup fixes.
  - `f2a462f`: final gate log.
- **Background state:**
  - No team members, tmux panes or worktrees remain.
  - No scratch server is running.
  - The Portainer stack `relume` (0.0.4) is live and owned by the user.

## 4. Current logic architecture (only what this step needs)

| Node | Responsibility | Key symbol | Notes |
|---|---|---|---|
| Gradle debug build | Builds `relume-debug.apk` | `android/app/build.gradle*` | versionName `dev` for debug |
| Release build (CI) | Builds the signed release APK and publishes it to the Relume GitHub releases | `.github/workflows/android.yml` (trigger `tags: ["companion-v*"]`, format `companion-vMAJOR.MINOR.PATCH`) | CI never runs tests (`scripts/ci_guard.py`) |
| Pre-push hook | `python3 scripts/ci_local.py`: guard plus `assembleRelease`, needs `ANDROID_HOME`; blocks the push on failure | `.githooks/` | The only remote is private `jhangyu/Relume` |

## 5. Data production and consumption chain

Not applicable: this step only builds and installs the app.

## 6. Type and interface contracts

Not applicable: no code changes.

Note for the user's own tests: the new Android app only works against server 0.0.4 or newer, because the old APIs were removed. The old companion v1.0.0 fails against 0.0.4.

## 7. Done

| Result | Verification | Anchor |
|---|---|---|
| [C] Linux gate (python plus shell) | `docker run ... relume-gate ... scripts/test.py --legs python,shell` → RC=0, 397 tests, skipped 0, 7.5 s | 374030d (`docs/logs/2026-10-26-mp-final-gate.md`) |
| [C] Android tests | `ANDROID_HOME=... python3 scripts/test.py --legs android` → RC=0, 42 tests | 374030d |
| [C] Removed-symbol greps | 10 of 10 RC=1 | 374030d |
| [C] Web front-end matches the mockups | review round 4 PASS | 8d7928b (`docs/logs/2026-10-26-fe-review-web.md`) |
| [C] Live deploy 0.0.4 | Migration 010 applied (user_version 10); 19 update and 13 build jobs succeeded after deploy | user-run release, verified by the lead |
| [P] Android front-end matches the mockups | Round 1 FAIL; fixes landed. Round 2 PARTIAL: 14 items confirmed fixed, 2 new must-fix items fixed in code (93b8752, 374030d) but not seen on the device | `docs/logs/2026-10-26-fe-review-android.md` "Round 2 (PARTIAL)" |

## 8. Open items

| Priority | Status | Item | Next action | Done when |
|---|---|---|---|---|
| P0 | [U] | Debug build at HEAD not yet on the phone | §1 steps 3–6 | `adb install` reports Success |
| P1 | [D] | Companion release (new signed APK for normal installs) | Ask the user. Releasing needs a push of 82 commits and a tag `companion-vX.Y.Z` (the last tag is `companion-v1.0.0`). Pushing and tagging are the user's call | User OK, push passes the pre-push hook, CI publishes the release |
| P2 | [U] | Screens the reviewer did not re-check on the device | The user checks them in their own testing: featured carousel at the top, Schedules "Last update run", app detail, download-source step, App settings, OTA schedule sheet, Settings | User feedback |

Parking lot (`docs/logs/2026-10-26-marketplace-contract.md`, bottom): **the user ruled every item unimportant; do not do them.**

## 9. Attempts, rulings and paths not to repeat

| Attempt | Result | Ruling | Retry? |
|---|---|---|---|
| Agent-driven device UI review (fe-review-android) | Slow; blocked twice by a locked phone | The user stopped it and tests on the device personally | No |
| Phone `cc5bf709` (PHY110) | Locked; install failed | The user moved to `10AFCF269C007FB` | No |
| Background scratch server | A hook blocks background processes | Scratch servers run inside a single command; the user can run `python3 -m webui serve` in their own terminal | Only in the user's terminal |
| macOS Python/shell suite | Fails because of the environment (no setpriv, killpg EPERM, BSD realpath) | Contract item 1 was removed by the user; the Linux gate is the judge | Not as a gate |

## 10. Future directions

None. The user declined the parking lot.

## 11. Known limits and uncertainties

- **Known limit:** the live DB has no backup taken right before migration 010. The only one is `local/data/webui.db.bak-2026-10-26`, from before the source cleanup.
- **Not verified on the device:** `93b8752` and `374030d`. They pass the unit tests and assembleDebug only.
- **Scratch test server (for the user):**
  - Run from `local/`: `RELUME_DATA=/Users/jhangyu/project/relume/.claude/tmp/fe-review-android/data RELUME_KEY=/Users/jhangyu/project/relume/.claude/tmp/fe-review-android/key python3 -m webui serve`. It listens on port 47000.
  - It has a scratch admin account on that copy only. If the credentials are lost, start it with `RELUME_ADMIN_PASSWORD=<new>`, which replaces the admin password (`auth.apply_env_password`).
  - Builds fail on macOS.
- **Needs a user decision:** whether to push and tag a companion release (P1).

## 12. Acceptance commands

```bash
git rev-parse --short HEAD                                   # f2a462f or newer
adb devices | grep 10AFCF269C007FB                            # ... device
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:assembleDebug --console=plain --no-watch-fs > .claude/tmp/mp-final/assemble.log 2>&1; echo RC=$?   # RC=0
adb -s 10AFCF269C007FB install -r android/app/build/outputs/apk/debug/relume-debug.apk                                                                                          # Success
curl -s localhost:65427/api/health                            # version 0.0.4
```

## 13. References

- Required: `docs/logs/2026-10-26-mp-final-gate.md` (final evidence), and `docs/logs/2026-10-26-fe-review-android.md` "Round 2 (PARTIAL)" (what was and wasn't seen on the device).
- Decisions: `docs/logs/2026-10-26-marketplace-decisions.md`, and `docs/logs/2026-10-26-marketplace-contract.md` (contract, parking lot not to be done).
- Spec and plan: `docs/superpowers/specs/2026-10-26-marketplace-design.md`, `docs/superpowers/plans/2026-10-26-marketplace.md`.
- Mockups (the UI reference): `docs/superpowers/mockups/marketplace/candidate-2/`, with `web/` used as is and `android/` revised.
- Scratch to delete once the user finishes testing:
  - `.claude/tmp/`: mp-*, fe-review-*, fix-*, mp-fix-*, be-api, research-*, icon-check, morphe-site, mp-migrate, mp-web, mp-lead, cleanup-sources.
  - Keep `.claude/tmp/fe-review-android/data` while the user uses the scratch test server.
