## Highlights

**companion-v1.4.0 → companion-v1.4.1** · 136 commits (18 Android-side)

The phone now tells the server about each tracked app's installed APK hash, and installs and removals use single-item calls (`/api/me/assignments/...`) instead of sending the whole list. Cards read the patch version from the server's install record. The app shows the APKMirror block end, the real download source and the follow-project errors. The server builds are tried by APKMirror versionCode until one patches, the Web UI follows a live event stream, and the Schedules screen shows the schedule's newest update run. **This release needs the new server image (the single-item routes answer 404 on an older server). The APK asset is now named `relume-1.4.1-universal.apk`.**

### ✨ Added

**Android app**
- Install assigns one item to the phone and removal unassigns one item through the single-item API; the full-list assignment is no longer used by the app.
- Sync reports a cached base APK sha256 per tracked app. Models carry the install record, the APK hash and the new server fields.
- The app settings screen edits the manual APKMirror versionCode list.
- Screens show the APKMirror block end, the real download source and the follow-project errors.

**Server and Web UI**
- `GET /api/events` change stream: Web UI pages refresh from it and the timed polls are gone. Devices scan on entry and have **Scan again**.
- The phone reports `apk_sha256` in sync and the reported hash decides the install record. Receipts become history only, and install records carry the patch version.
- A phone can assign and unassign one item or project; the full-list PUT is kept.
- The server reads package, version and signer certificate SHA-256 (v3 or v2 signing block) of each build output and hashes the stored file.
- APKMirror variants are tried by versionCode until one patches, the winner is remembered, and a manual versionCode can pin it.
- An app's patch bundle can be pinned to one release tag from the settings panel, and Apps detail switches the patch source channel between stable and prerelease.
- A follow project's first-known package unassigns same-package apps on its phones and sends a notification. App objects carry the selected and the actual download source. The download error is kept apart from the check error.
- The APKMirror block end is stored in settings and survives a restart. A failed store icon is fetched again after 7 days.
- ADB OTA reads the base APK sha256 of tracked packages.
- `GET /api/jobs` filters by `schedule_id` and names the expected integer in its 400. Auth state serves the client limits (minimum password, ABIs, engine kinds, sigbypass maximum).
- The last run of a schedule shows the newest update job, including manual runs.

### 🛠 Fixed & Improved

**Android app**
- The Schedules screen's last update run asks the server for the schedule's newest upstream job (`kind=upstream&schedule_id&limit=1`) instead of scanning the newest 20 jobs.
- Sign-out resets every account-bound field, so the next account no longer sees the previous one's devices, catalog status, clean-up report or prompts.
- The reinstall title follows the step's own error code, and the phone no longer reads the server's `signer_conflict` and `downgrade` flags.
- The card reads the patch version from the server's install record; the local record is deleted.
- App settings read ABIs and engine limits from the server instead of a local copy.
- The needs-reinstall notification is posted by the one step transition that sets the state.

**Server and Web UI**
- A morphe build whose patches did not apply fails and the previous APK stays published. A variant loop that runs out of variants ends as `patch_items_failed`.
- The published build is the newest successful build whose APK file exists, for status, guard and OTA. A queued build of a disabled app ends as `app_disabled`.
- FlareSolverr and CFBypass are asked once each per page and the service error is logged. The build runner uses a fixed user agent instead of scraping a third-party site.
- The server owns the APK cache file names and clean-up keeps exactly the recorded files; a prune error is logged and never rejects the registered build.
- Module start hooks run before the job workers, so the DATA/work sweep cannot delete a live job's scratch directory. The job worker stays alive when a job fails outside its handler.
- An unreadable `adb` package list fails the OTA job. App-row responses no longer spawn `adb devices` under the global adb lock. A phone sync computes its plan once.
- The package-conflict check and the assignment write share one transaction. A failed COMMIT in `db.tx` rolls back and re-raises.
- Web pages show swallowed errors instead of hiding them (settings, marketplace, sign-in, devices, apps drawer, OTA all). Pages load once through the event stream and no longer reload after their own writes. The Schedules page follows the stream.
- The sidebar polls one small `/api/shell` endpoint instead of four calls. The scheduler waits until the next due run instead of a fixed 30 s tick.
- A secret file that exists but cannot be read, or whose stat fails, raises with its path instead of reading as not set. A secret path under a file says a parent is not a directory.
- Blank query values are kept, so a blank integer parameter gets the 400.
- Start removes `.staged-*` leftovers of a crashed artifact copy.
- The browser image builds with cloakbrowser 0.6.5, because 0.7.0 needs a license key.

### ⚙️ Internal / Build

- Schema migrations 018 to 022 (legacy flag derived, device protocol, APKMirror block, follow errors, `device_packages.apk_path`).
- The build runner writes only name, abi and file per variant; the server reads the APK info. Keystore credentials come from the server's keystore table in `job.json`.
- Many refactors in the server, Web UI and Android app (shared helpers, named constants, deleted dead code). Static/UI tests were dropped and a static rule bans swallowed catches.
- Design records: `docs/logs/2026-11-02-decisions/conclusion.md` and `docs/logs/2026-11-02-followups/conclusion.md`.

### 📦 Install / Upgrade

- **Server first:** the new server image must be running (already deployed). An older server answers 404 on the single-item assignment routes and on `GET /api/jobs?schedule_id=`.
- **Android:** download `relume-1.4.1-universal.apk` from this release and install it over the old one. The asset was named `relume-companion.apk` before.
- A phone still on 1.4.0 keeps working against the new server.

---

## 版本重點

**companion-v1.4.0 → companion-v1.4.1** · 共 136 個 commit（Android 端 18 個）

手機現在會向伺服器回報每個追蹤應用程式已安裝 APK 的雜湊，安裝與移除改用單一項目的呼叫（`/api/me/assignments/...`），不再送出整份清單。卡片的修補版本讀自伺服器的安裝紀錄。App 會顯示 APKMirror 封鎖結束時間、實際下載來源與追蹤專案的錯誤。伺服器建置會依 APKMirror versionCode 逐一嘗試直到修補成功，Web UI 跟隨即時事件串流，排程畫面顯示該排程最新的更新執行。**本版需要新的伺服器映像（舊伺服器上單一項目路由會回 404）。APK 檔名改為 `relume-1.4.1-universal.apk`。**

### ✨ 新增

**Android App**
- 安裝以單一項目 API 指派一個項目，移除則取消指派一個項目；App 不再使用整份清單的指派。
- 同步時回報每個追蹤應用程式的快取 base APK sha256。模型帶有安裝紀錄、APK 雜湊與新的伺服器欄位。
- 應用程式設定畫面可編輯手動的 APKMirror versionCode 清單。
- 畫面顯示 APKMirror 封鎖結束時間、實際下載來源與追蹤專案的錯誤。

**伺服器與 Web UI**
- 新增 `GET /api/events` 變更串流：Web UI 頁面由它更新，定時輪詢已刪除。裝置頁進入時掃描，並有 **Scan again**。
- 手機在同步時回報 `apk_sha256`，由回報的雜湊決定安裝紀錄；收據僅作為歷史，安裝紀錄帶有修補版本。
- 手機可單獨指派與取消指派一個項目或專案，整份清單的 PUT 仍保留。
- 伺服器讀取每個建置產出的套件、版本與簽章憑證 SHA-256（v3 或 v2 簽章區塊），並對儲存的檔案計算雜湊。
- APKMirror 變體會依 versionCode 逐一嘗試直到修補成功，成功者會被記住，也可手動固定 versionCode。
- 應用程式的修補套件可在設定面板固定到單一發佈標籤，應用程式詳情可在穩定版與預發佈版之間切換修補來源頻道。
- 追蹤專案第一次得知套件時，會取消其手機上相同套件的應用程式指派並發出通知。應用程式物件帶有所選與實際的下載來源，下載錯誤與檢查錯誤分開保存。
- APKMirror 封鎖結束時間存入設定，重啟後仍保留。失敗的商店圖示 7 天後會重新擷取。
- ADB OTA 讀取追蹤套件的 base APK sha256。
- `GET /api/jobs` 可用 `schedule_id` 篩選，400 訊息會指出預期的整數。認證狀態提供用戶端限制（密碼最小長度、ABI、引擎種類、sigbypass 上限）。
- 排程的最近一次執行顯示最新的更新工作，包含手動執行。

### 🛠 修正與改善

**Android App**
- 排程畫面的最近一次更新執行，改向伺服器要求該排程最新的 upstream 工作（`kind=upstream&schedule_id&limit=1`），不再掃描最新 20 筆工作。
- 登出會重設所有與帳號綁定的欄位，下一個帳號不會再看到前一個帳號的裝置、目錄狀態、清理報告或提示。
- 重裝標題依步驟自己的錯誤碼決定，手機不再讀取伺服器的 `signer_conflict` 與 `downgrade` 旗標。
- 卡片的修補版本讀自伺服器安裝紀錄，本機紀錄已刪除。
- 應用程式設定的 ABI 與引擎限制改讀自伺服器，不再使用本機副本。
- 「需要重裝」通知由設定該狀態的唯一步驟轉換發出。

**伺服器與 Web UI**
- 修補未套用的 morphe 建置會失敗，先前的 APK 保持發佈。變體用盡的迴圈以 `patch_items_failed` 結束。
- 發佈的建置是 APK 檔案仍存在的最新成功建置，用於狀態、守門與 OTA。已停用應用程式的排隊建置以 `app_disabled` 結束。
- FlareSolverr 與 CFBypass 每頁各詢問一次並記錄服務錯誤。建置執行器使用固定 user agent，不再每次建置擷取第三方網站。
- 伺服器掌管 APK 快取檔名，清理只保留已記錄的檔案；清理錯誤只記錄，不會拒絕已登記的建置。
- 模組啟動掛鉤先於工作 worker 執行，DATA/work 清掃不會刪除執行中工作的暫存目錄。工作在處理器之外失敗時，worker 仍會存活。
- 無法讀取的 `adb` 套件清單會使 OTA 工作失敗。應用程式紀錄的回應不再在全域 adb 鎖下啟動 `adb devices`。手機同步只計算一次計畫。
- 套件衝突檢查與指派寫入共用同一個交易。`db.tx` 的 COMMIT 失敗會回滾並重新拋出。
- Web 頁面會顯示被吞掉的錯誤（設定、市集、登入、裝置、應用程式抽屜、OTA 全部）。頁面透過事件串流載入一次，不再於自己的寫入後重新載入。排程頁跟隨串流。
- 側邊欄改輪詢單一小型 `/api/shell` 端點，不再呼叫四個。排程器等到下一次到期執行，不再固定 30 秒輪詢。
- 存在但無法讀取、或 stat 失敗的秘密檔案，會連同路徑拋出錯誤，不再讀成未設定。位於檔案之下的秘密路徑會指出上層不是目錄。
- 空白的查詢值會被保留，因此空白的整數參數會得到 400。
- 啟動時移除當機的產出複製留下的 `.staged-*`。
- 瀏覽器映像以 cloakbrowser 0.6.5 建置，因為 0.7.0 需要授權金鑰。

### ⚙️ 內部/建置

- 結構遷移 018 至 022（舊版旗標改為推導、裝置協定、APKMirror 封鎖、追蹤錯誤、`device_packages.apk_path`）。
- 建置執行器每個變體只寫入 name、abi 與 file，APK 資訊由伺服器讀取。簽署金鑰憑證由 `job.json` 中伺服器的 keystore 資料表提供。
- 伺服器、Web UI 與 Android App 有大量重構（共用輔助函式、具名常數、刪除無用程式碼）。靜態/UI 測試已刪除，並新增禁止吞例外的靜態規則。
- 設計紀錄：`docs/logs/2026-11-02-decisions/conclusion.md` 與 `docs/logs/2026-11-02-followups/conclusion.md`。

### 📦 安裝／升級

- **先升級伺服器：** 新的伺服器映像必須已在執行（已部署）。舊伺服器對單一項目指派路由與 `GET /api/jobs?schedule_id=` 會回 404。
- **Android：** 從本次發佈下載 `relume-1.4.1-universal.apk` 並覆蓋安裝。此檔案先前名為 `relume-companion.apk`。
- 仍在 1.4.0 的手機可繼續搭配新伺服器使用。
