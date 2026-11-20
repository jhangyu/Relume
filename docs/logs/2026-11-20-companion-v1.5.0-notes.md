## Highlights

**companion-v1.4.2 → companion-v1.5.0** · 18 commits (3 Android-side)

The new **App Manage** page in the Web UI shows every user-installed app on every device in one table, compared against one primary device. From there you install, upgrade and remove apps on each device. The phone now reports its app list with sizes and carries out those actions after every sync. The check interval gains a 15-minute option. **This release needs the new server image (App Manage, migrations 024 and 025). The APK asset is named `relume-1.5.0-universal.apk`.**

### ✨ Added

**Android app**
- Each sync reports all user-installed apps on the phone with label, version, icon and size (app, data and cache), plus the phone's system language.
- Sizes need Usage access. Settings has a new **Usage access** row with an **Allow** button that opens the system screen. Without the permission, sizes show as unknown, never as 0.
- After every sync the phone runs the actions queued on the App Manage page and reports each result (done, cancelled by the user, or failed with the reason):
  - Relume build: installs through the existing install flow.
  - Google Play direct: streams the base APK and its split APKs from Google straight into one install session; no file is cached. The splits include the phone's language split (for example `config.zh` on a zh-TW phone).
  - Play Store page: posts a notification that opens the app's Play Store page.
  - Remove: shows the system uninstall confirmation.
- A new **15 min** check interval; the default interval is unchanged.

**Server and Web UI**
- The **App Manage** page: apps are rows and devices are columns. You pick the primary device (stored on the server). Column widths can be resized and are remembered per browser. Apps that are not on the primary device come first, largest first.
- Each cell shows the version and size, or Install. It shows Upgrade when its version is lower than the highest version on any device or the latest Relume build. Pending, failed and offline states are shown on the cell.
- Batch install and batch remove from the primary device's cell, with device checkboxes.
- The server resolves Google Play download URLs for the phone, using the phone's locale.
- ADB devices run actions at once as server jobs. Their app list comes from `pm` and `dumpsys`.
- New `appmanage` event topic and sidebar entry.

### 🛠 Fixed & Improved

**Server and Web UI**
- A regression verdict from the patch guard on an automatic APKMirror variant now tries the next variant, so the normal build is tried after a lite build regressed.
- The upstream browser image pins playwright 1.64.0, because 1.65 removed the module cloakbrowser 0.6.5 imports.

### ⚙️ Internal / Build

- Schema migrations 024 (App Manage tables) and 025 (device locale).
- Design records: `docs/superpowers/specs/2026-11-20-app-manage-design.md` and `docs/logs/2026-11-20-app-manage-signoff.md`.

### 📦 Install / Upgrade

- **Server first:** the new server image must be running (App Manage is a server feature).
- **Android:** download `relume-1.5.0-universal.apk` from this release and install it over 1.4.2. Signing is unchanged, so no reinstall is needed. Open Relume once, then grant Usage access in Settings to see app sizes.
- Actions run at the phone's next sync, so use the new 15-minute interval or sync by hand to see them sooner.
- Removal needs your confirmation on the phone. With notifications off and the app in the background, the uninstall prompt waits until the app is opened again.
- ADB-only apps have no label or icon, and ADB actions run one at a time.
- A phone still on 1.4.2 keeps working against the new server but does not appear with an app list.

---

## 版本重點

**companion-v1.4.2 → companion-v1.5.0** · 共 18 個 commit（Android 端 3 個）

Web UI 新增 **App Manage** 頁面，以一張表顯示每台裝置上所有使用者安裝的應用程式，並以一台主要裝置為基準比較。你可以從這裡在各裝置上安裝、升級與移除應用程式。手機現在會回報含大小的應用程式清單，並在每次同步後執行這些動作。檢查間隔新增 15 分鐘選項。**本版需要新的伺服器映像（App Manage、遷移 024 與 025）。APK 檔名為 `relume-1.5.0-universal.apk`。**

### ✨ 新增

**Android App**
- 每次同步回報手機上所有使用者安裝的應用程式，含名稱、版本、圖示與大小（程式、資料與快取），並回報手機的系統語言。
- 大小需要「使用情況存取權」。設定畫面新增 **Usage access** 列，**Allow** 按鈕會開啟系統設定畫面。未授權時大小顯示為未知，而不是 0。
- 每次同步後，手機會執行 App Manage 頁面排入的動作，並回報每個結果（完成、使用者取消、或附原因的失敗）：
  - Relume 建置：走既有的安裝流程。
  - Google Play 直接下載：從 Google 將 base APK 與其 split APK 直接串流進同一個安裝工作階段，不快取檔案。split 包含手機的語言 split（例如 zh-TW 手機的 `config.zh`）。
  - Play 商店頁面：發出通知，點擊後開啟該應用程式的 Play 商店頁面。
  - 移除：顯示系統的解除安裝確認。
- 新增 **15 min** 檢查間隔；預設間隔不變。

**伺服器與 Web UI**
- **App Manage** 頁面：應用程式為列，裝置為欄。可選擇主要裝置（儲存在伺服器），欄寬可調整並於各瀏覽器記住。不在主要裝置上的應用程式排在前面，由大到小。
- 每個儲存格顯示版本與大小，或 Install。版本低於任一裝置的最高版本、或 Relume 最新建置時，顯示 Upgrade。儲存格也會顯示等待中、失敗與離線狀態。
- 可由主要裝置的儲存格批次安裝與批次移除，附裝置勾選。
- 伺服器為手機解析 Google Play 下載網址，並使用手機的語系。
- ADB 裝置即時執行動作（以伺服器工作執行），其應用程式清單來自 `pm` 與 `dumpsys`。
- 新增 `appmanage` 事件主題與側邊欄項目。

### 🛠 修正與改善

**伺服器與 Web UI**
- 修補守門對自動 APKMirror 變體的退步判定，現在會改試下一個變體，因此 lite 建置退步後會改試一般建置。
- 上游瀏覽器映像固定 playwright 1.64.0，因為 1.65 移除了 cloakbrowser 0.6.5 所匯入的模組。

### ⚙️ 內部/建置

- 結構遷移 024（App Manage 資料表）與 025（裝置語系）。
- 設計紀錄：`docs/superpowers/specs/2026-11-20-app-manage-design.md` 與 `docs/logs/2026-11-20-app-manage-signoff.md`。

### 📦 安裝／升級

- **先升級伺服器：** 新的伺服器映像必須已在執行（App Manage 是伺服器功能）。
- **Android：** 從本次發佈下載 `relume-1.5.0-universal.apk` 並覆蓋安裝於 1.4.2 之上。簽章不變，不需重裝。開啟 Relume 一次，再到設定授予 Usage access 即可看到應用程式大小。
- 動作在手機下一次同步時執行，可使用新的 15 分鐘間隔或手動同步以更快看到結果。
- 移除需要在手機上確認。通知關閉且 App 在背景時，解除安裝提示要等到下次開啟 App。
- 僅 ADB 的應用程式沒有名稱與圖示，ADB 動作一次只執行一個。
- 仍在 1.4.2 的手機可繼續搭配新伺服器使用，但不會出現應用程式清單。
