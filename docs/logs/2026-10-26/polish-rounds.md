# Polish batch — rounds and file ownership

Contract: `docs/logs/2026-10-26/polish-contract.md`. Spec: `docs/superpowers/specs/2026-10-26-polish-batch-design.md`.
One owner per file per round. Touching a file outside your list = stop and report.

## Lead rulings on the architects' open items
- No new tests in this batch (user rule). The pairing plan's new test and the Server URL plan's `ServerCompatTest.kt` are dropped.
- Web inline icon SVGs on pages are swapped to the sprite by the web-controls slice that owns that page. The icons implementer owns only `app.js`; it adds `send` and `lock` to the sprite. The `.i` stroke-to-fill rows in `app.css` (icon plan §3: lines 70–72, 156, 249–253) are applied by web-controls slice 1, which owns `app.css`.
- GitHub icon: neutral Fluent `code` path under the id `github`.
- Followed apps, web Install: one click installs on every ADB phone assigned to the project (same as the Followed projects page). No per-phone picker.
- Followed apps install only on phones assigned to the project.
- Badge does not count followed apps (web and Android).
- Android Server URL change signs out locally only; the old token is not revoked.
- Requirement 9 on Android needs no change (already auto-loads, `MarketplaceScreen.kt:124-130`). Requirement 9 on web is done by web-controls slice 1 (owns `index.js`).

## Round 1
| Member | Plan | Files owned |
|---|---|---|
| impl-web-icons | `polish-plan-web-icons.md` (sprite part), paths in `polish-icons-paths.md` | `local/webui/static/app.js` |
| impl-web-ctl-a | `polish-plan-web-controls.md` slice 1, icon plan rows for its files, spec req 9 (web) | `app.css`, `apps.html`, `index.html`, `index.js`, `schedules.html`, `schedules.js` |
| impl-web-ctl-b | `polish-plan-web-controls.md` slice 2, icon plan rows for its files, test deletions from both web plans | `projects.html`, `projects.js`, `settings.html`, `devices.html`, `devices.js`, `login.html`, `builds.html`, `local/webui/tests/test_ui_static.py` |
| impl-be-pairing | `polish-plan-be-pairing.md` (no new test) | `local/webui/ota.py` |
| impl-android-url | `polish-plan-android-url.md` (no new test) | `RelumeApi.kt`, `LoginScreen.kt`, `SettingsScreen.kt` |

## Round 2
| Member | Plan | Files owned |
|---|---|---|
| impl-be-apps | `polish-plan-be-apps.md` backend part | `projects.py`, `ota.py`, `market.py`, `tests/test_market_api.py` |
| impl-web-followed | `polish-plan-be-apps.md` web part + web-controls §2 apps.js handoff | `apps.js`, `apps.html` (lines of the followed list only) |
| impl-android-apps | `polish-plan-android-apps.md` | `Models.kt`, `RelumeApi.kt`, `AppStatus.kt`, `AppModel.kt`, `AppsScreen.kt` |

After round 2: backend reviewer, then debug APK build and install on `10AFCF269C007FB`.
