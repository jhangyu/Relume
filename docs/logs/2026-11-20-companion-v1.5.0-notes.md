## Highlights

**companion-v1.4.2 → companion-v1.5.0** · 14 commits. Android side: 3 changes, all for App Manage.

The new **App Manage** page in the web UI shows every user-installed app on every device in one table, compared against one primary device. From there you install, upgrade and remove apps on each device. This APK lets the phone report its app list and carry out those actions.

- **Server:** the server image must already include App Manage (migrations 024 and 025).
- **APK asset:** `relume-1.5.0-universal.apk`.

### ✨ New

**Android app**
- **App list:** each sync reports all user-installed apps on the phone, with label, version, icon and size (app, data and cache). The phone also reports its system language.
- **Sizes:** they need Usage access. Settings has a new **Usage access** row with a button that opens the system screen. Without the permission, sizes show as unknown.
- **Actions from the web page:** the phone runs the actions queued on the App Manage page after every sync and reports each result.

  | Source | What the phone does |
  |---|---|
  | Relume build | Installs through the existing install flow. |
  | Google Play direct | Streams the APK and its split APKs from Google straight into one install session. No file is cached. |
  | Play Store page | Shows a notification that opens the app's Play Store page. |
  | Remove | Shows the system uninstall confirmation. |

  The split APKs include the phone's language split, for example `config.zh` on a zh-TW phone.
- **Check interval:** a new **15 min** option. The default interval is unchanged.

**Server** (no app action needed)
- **App Manage page:** apps are rows and devices are columns. You pick the primary device. Column widths can be resized and are remembered. Apps that are not on the primary device come first, largest first.
- **Cell states:** each cell shows the version and size, or Install. It shows Upgrade when the version is lower than the highest version on any device or the latest Relume build. It also shows pending, failed and offline states.
- **Batch:** batch install and batch remove from the primary device's cell.
- **ADB devices:** they run actions at once, and their app list comes from `pm` and `dumpsys`.

### 📦 Install / Upgrade

Install `relume-1.5.0-universal.apk` over 1.4.2. Signing is unchanged, so no reinstall is needed. After installing, open Relume once. Then grant Usage access in Settings to see app sizes.
