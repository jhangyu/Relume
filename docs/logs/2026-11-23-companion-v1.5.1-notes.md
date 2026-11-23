## Highlights

**companion-v1.5.0 → companion-v1.5.1** · 3 commits (1 Android-side)

The app opened slowly since 1.5.0, because every sync also read all installed apps (sizes and icons) and uploaded the icons. Now startup and the regular sync report only the apps Relume manages. The full installed-app list for App Manage is sent by a separate background job after each sync, so opening the app and the main features no longer wait for it. **No server update is needed. The APK asset is named `relume-1.5.1-universal.apk`.**

### 🛠 Fixed & Improved

**Android app**
- Startup, install finish and the periodic check report only the Relume-managed apps. They no longer read all installed apps or upload icons.
- A new background job runs after every sync. It reads all user-installed apps, sends the changes to the server, uploads icons, saves the snapshot and runs the App Manage actions the server returns. It does not hold the sync lock, so it never blocks the app.

### ⚙️ Internal / Build

- Design records: `docs/logs/2026-11-23-inventory-detach-contract.md` (contract and sign-off).

### 📦 Install / Upgrade

- **Server:** no update needed; 1.5.1 works with the 1.5.0 server image.
- **Android:** download `relume-1.5.1-universal.apk` from this release and install it over 1.5.0. Signing is unchanged, so no reinstall is needed. There are no permission changes.
- App Manage actions (install, upgrade, remove) queued on the web page run after the background app-list job finishes, so they can start slightly later than before.

---

## 版本重點

**companion-v1.5.0 → companion-v1.5.1** · 共 3 個 commit（Android 端 1 個）

自 1.5.0 起 App 開啟變慢，原因是每次同步還會讀取所有已安裝的應用程式（大小與圖示）並上傳圖示。現在啟動與一般同步只回報 Relume 管理的應用程式。App Manage 所需的完整已安裝應用程式清單，改由每次同步後的獨立背景工作送出，因此開啟 App 與主要功能不再等待它。**不需要更新伺服器。APK 檔名為 `relume-1.5.1-universal.apk`。**

### 🛠 修正與改善

**Android App**
- 啟動、安裝完成與定期檢查只回報 Relume 管理的應用程式，不再讀取所有已安裝的應用程式，也不再上傳圖示。
- 每次同步後會執行新的背景工作：讀取所有使用者安裝的應用程式，將變更送到伺服器，上傳圖示、儲存快照，並執行伺服器回傳的 App Manage 動作。它不佔用同步鎖，因此不會阻塞 App。

### ⚙️ 內部/建置

- 設計紀錄：`docs/logs/2026-11-23-inventory-detach-contract.md`（契約與簽收）。

### 📦 安裝／升級

- **伺服器：** 不需更新；1.5.1 可搭配 1.5.0 的伺服器映像。
- **Android：** 從本次發佈下載 `relume-1.5.1-universal.apk` 並覆蓋安裝於 1.5.0 之上。簽章不變，不需重裝，權限也沒有變動。
- 從網頁排入的 App Manage 動作（安裝、升級、移除）會在背景應用程式清單工作完成後才執行，因此可能比以前稍晚開始。
