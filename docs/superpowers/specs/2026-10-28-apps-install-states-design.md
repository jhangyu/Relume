# Apps install states, install lightbox, reinstall, daily check — design

Date: 2026-10-28. Scope: Relume Android app (`android/`) plus the server fields it needs.

## 1. Card action states
One pure function decides the phone-side action for every app card (items and followed projects share it; `ui/AppStatus.kt`):

| Condition (checked in order) | Card shows |
|---|---|
| package not installed on the phone | **Install** |
| installed, signing cert differs from the server build | **Uninstall & reinstall** |
| installed, app version older OR patch version older than the server build | **Upgrade** |
| otherwise | "Installed <version>" pill |

- Bug to fix: a not-installed app shows no Install button. Suspected cause: `AppFacts.installable` (`ui/AppStatus.kt:69`) needs a plan step with INSTALL, or no step plus `appVersion`; other step states drop the button. Confirm on the device before the fix.
- Server exposes per app: patch version and signing cert fingerprint of the current build.
- The phone records the patch version it installed for each package (written when Relume installs a package).
- A build in progress still hides the action (unchanged).

## 2. Install lightbox
- The full-page `Screen.Install` becomes a modal lightbox over the Apps tab: per-step download / verify / install progress.
- Success: the lightbox closes itself and the Apps cards refresh, so the user can start the next install.
- Failure: the lightbox stays open with the error and Retry / Close.
- The visual design comes from one of two HTML mockups (designers keep the current Relume palette and components); the user picks one.

## 3. Uninstall & reinstall (signer mismatch)
- The existing confirm dialog stays; its text states that the app's data and login are erased.
- Confirm → system uninstall prompt → on uninstall success the install of the server build starts automatically → lightbox → back to Apps.
- User cancels the system uninstall prompt → the flow stops, the card is unchanged.

## 4. Daily scheduled check
- The check interval gains "Daily at <time>" next to 1 h / 6 h / 24 h. The user picks the time, default 03:15.
- Implementation: WorkManager periodic work, 24 h, initial delay to the next chosen time; constraints charging + unmetered network. The time is approximate (Doze maintenance windows), not an exact alarm.
- Background runs keep the existing silent update (`USER_ACTION_NOT_REQUIRED`): silent only when Relume is the installer of record, same signer, and an upgrade.

## Out of scope
- Server push (FCM / UnifiedPush): decided against; the scheduled check covers it.
- Exact-time alarms (`SCHEDULE_EXACT_ALARM`).

## Verification
- One unit test for the action function: the four states plus patch-version comparison.
- Lightbox, reinstall chain and daily check: one real run each on the device, not committed as tests.
