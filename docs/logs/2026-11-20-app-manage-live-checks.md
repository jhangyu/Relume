# App Manage — live checks (user runs on real devices)

Before any check: release the companion (tag `companion-vX.Y.Z`) and install the new APK on the phone, and release and redeploy the server image (migrations 024 and 025 run on start). The user runs both. Contract: `docs/logs/2026-11-20-app-manage-contract.md`. Plan §4: `docs/superpowers/plans/2026-11-20-app-manage-plan.md`.

Fill the Result column with pass / fail and a note.

| # | Steps | Expected | Result |
|---|---|---|---|
| 1 | Open the web UI after the redeploy. Open `/api/appmanage` while logged in (or open the App manage page). | The server starts with migration 024. The response lists every non-legacy device and no legacy device. | |
| 2 | Open the updated companion once so it syncs. Open the App manage page. Look at the phone's column before and after you grant usage access (Android Settings → Apps → Special access → Usage access → Relume). Sync again. | The phone shows its user apps with labels, icons and sizes. Before the grant the sizes read "unknown: usage access not granted". After the grant and a sync the sizes are real numbers. | |
| 3 | Pick an ADB device. Click Refresh on the App manage page. | Its user apps appear with version names. Sizes are numbers, or show a `dumpsys diskstats` reason. | |
| 4 | Set one device as the primary. | Row groups and order follow plan §1.7: rows of the primary first, apps missing on the primary after, highest version and outdated flags shown. | |
| 5 | On the companion phone, queue Remove for an app. Confirm the system dialog on the phone. | The system uninstall dialog appears. After the result and the next sync the cell clears. | |
| 6 | On the ADB device, queue Remove for an app. | The app is uninstalled and the cell clears. | |
| 7 | Queue Install for a Relume-tracked app on another device (source build). | The assignment appears on the Devices page. The install completes: for ADB through the job, for the companion through the install sheet or the background run. | |
| 8 | Cross-IP Play URL: turn Wi-Fi off on the companion phone (mobile data only). Queue Install with source `play_direct` for an app Relume does not track. | The app installs. If the install fails with HTTP 403 from `play.googleapis.com`, the URLs are bound to the server IP. Stop: the in-app Play client is the follow-up (out of scope). | |
| 9 | Queue Install with source `play_direct` for an app on the ADB device. | The app installs. The server streams the download. | |
| 10 | On the companion phone, queue Install with source `play_page`. | A notification appears. Tapping it opens the Play Store page of the app. | |
| 11 | Open the companion Settings. Open the check interval list. | "15 min" can be selected. The default interval did not change. | |
| 12 | Turn the phone offline (airplane mode) or close the companion. Queue an action for it. Turn the phone back online and wait for the next sync (or open the companion). | The cell shows "waiting for phone sync" while the phone is offline. The action runs at the next sync. | |
| 13 | Use a phone whose system language is Traditional Chinese (zh-TW). Sync once so the server stores its locale. Queue Install with source `play_direct` for an app that has a language split (for example one with a `config.zh` split on Play). After the install run `adb shell pm path <package>` or `adb shell dumpsys package <package>`. | The installed app has a `config.zh*` split next to the base and the other splits. The server side is proven in `docs/logs/2026-11-20-play-url-probe.md` ("Locale probe"). | |
