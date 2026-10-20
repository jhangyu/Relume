# X (Piko) 登入卡住：根因與權杖匯入繞過

日期：2026-10-20
裝置：vivo X Fold6（型號 PD2545 / V2545A，中國版韌體，`ro.vivo.product.overseas=no`，bootloader 上鎖、verifiedboot green、release-keys、未 root）

## 症狀
官方 X 與 Piko X 皆無法登入。點「登入」後：
- 新版 X Lite 介面（`com.twitter.x.lite.XLiteActivity`）：一直轉圈，進不了輸入帳號畫面。
- 關掉 App 認證後可進到輸入帳號，但送出帳號時回「我們無法驗證你的請求」。

## 根因
X 伺服器拒絕本機送出的 Play Integrity 驗證結果，回 `BadRequest: Validation Failed, code: 214`。
X 新版登入流程硬性要求 App 認證權杖，拿不到就按退避策略無限重試 → 轉圈。此問題在手機端無法修復。

### 排除過程（已確認非以下原因）
- 網路、Private DNS（`dns.jhangy.us` 只擋 Firebase/Crashlytics 遙測）、30+ 個 X 相關網域解析皆正常。
- 裝置已通過 Play 認證、Play Integrity 判定全過。
- Play 商店裝的 X 簽章正確，非竄改。
- 系統對 X 這個 App 無網路限制。
- 在 Play 商店 `IExpressIntegrityServiceCallback` 回呼下中斷點：Integrity 權杖**成功**回傳，無錯誤碼 → 問題不在產生權杖，而在伺服器端驗證。

### 證據取得方式
1. 用 `APKEditor.jar` 反編譯 release Piko，在 manifest 加 `android:debuggable="true"`（需在 `public.xml` 補一個未使用的 `xml` 資源 id，否則 build 報 "Local resource not defined"），重打包後用 **release 的 Morphe key** 重簽（見下方重簽細節）。
2. jadx 反編譯定位：卡住期間不斷要 Integrity 的是 `com.twitter.app_attestation.w`（UserAppAttestTokenProvider，原 class 名由 `@DebugMetadata` 還原），呼叫鏈 `w.e(...)` → Play Integrity。
3. X 內部 logger `com.twitter.util.log.c` 在 release 被導向空 sink（`e().a` 為空 list）。用自製 JDI 工具 `XLogTap.java` 在這些 static 方法與 `w.e` 下斷點攔截參數，讀到完整認證流程：
   - `Got tokenProvider, requesting nonce from graphql` → `Got attestation nonce` → `Got attestationObj, sending to graphql` → **`Failed to get attestation token: IllegalStateException BadRequest: Validation Failed, code: 214`**。
   - ART 無 local-var table，JDI 讀參數需用 `com.sun.tools.jdi.LocalVariableImpl` 指定 slot（參數位於最後 `ins` 個 vreg，`regs`/`ins` 由 `dexdump` 取得）。

## 繞過方案：登入權杖匯入
Piko 內建 `Import/Export login token` patch（`app.morphe.extension.twitter.patches.logintoken.ImportExportLoginTokenPatch`），匯入權杖後直接建立 Android 帳號，**完全不經過伺服器登入驗證**，因此避開 214。

匯入 UI 按鈕只加在舊版 OCF 歡迎頁（`ocf_cta_step_dynamic`），本機走 X Lite 介面不顯示該頁 → 按鈕永遠不出現。Piko 的 feature-flag 覆寫（`piko_settings.xml` 的 `misc_feature_flags`，格式 `flag:false,...`）無法把介面切回舊版。

### 實際執行步驟
1. 來源手機（另一台已登入的 Piko）：Piko 設定 → 匯出登入權杖 → 選帳號 → 儲存為檔案 → `/sdcard/Download/piko_account_<user>.json`。
   - 格式：`{username, token, secret, userdata{...}}`。此檔即帳號登入憑證，等同密碼。
2. 目標手機裝 **debuggable + Morphe-key 重簽**的 Piko（覆蓋前須先 uninstall 舊簽章版）。
3. 自製 JDI 工具 `PikoImport.java` 附加到 X 程序：在 `android.os.Handler.dispatchMessage`（main thread）斷一次取得 suspended thread，透過 app classloader `Class.forName` 載入 patch class，呼叫
   `ImportExportLoginTokenPatch.addAccount(Context, String)` 傳入權杖 JSON。回報 `addAccount invoked OK`。
4. 重啟 X → 直接進時間軸，登入成功。
5. 用 **release 版** `twitter-stable-piko.apk`（同 Morphe key，簽章相符）`pm install -r -d` 覆蓋安裝，保留帳號資料、移除 debuggable。
   - `pm install -r` 可能回 RC=1 但僅 dexopt profile checksum 警告（benign），實際已安裝；以 `dumpsys package` 的 versionName 與 `run-as ... → not debuggable` 確認。

### Morphe key 重簽細節
- Keystore：`src/keystore/morphe.keystore`，格式 **BKS**，storepass 空、別名 `Morphe`、keypass `Morphe`。SHA-256 `b190...84e9`，與 release APK 一致。
- 需 BouncyCastle provider（`bcprov-jdk18on` 1.80）：
  `java -cp <bt>/lib/apksigner.jar:bcprov.jar com.android.apksigner.ApkSignerTool sign --ks morphe.keystore --ks-type BKS --ks-provider-class org.bouncycastle.jce.provider.BouncyCastleProvider --ks-pass pass: --key-pass pass:Morphe --ks-key-alias Morphe --out <out> <aligned.apk>`

## 已知限制
- 這是繞過登入，非正常登入。使用中若某些動作（發文、改設定）再觸發 App 認證，仍可能被 214 擋。日常瀏覽實測正常。
- 須關閉 Play 商店對 X 的自動更新，否則官方版會持續嘗試覆蓋（簽章不同，失敗）。

## 善後
- 除錯期間裝的 mitmproxy user CA 憑證需在「設定 → 安全性 → 加密與憑證 → 使用者憑證」手動移除（可解密該機 HTTPS）。
- 權杖副本（Mac、來源手機 Download）已刪除。
- 另一台來源手機未改動。

## 工具檔（置於 textify/scripts/tmp，gitignored）
- `XLogTap.java`：JDI 攔截 X 內部 log 與 app-attest 流程。
- `PikoImport.java`：JDI 呼叫 `addAccount` 匯入權杖。
- `jdb_integrity.py` / `jdb_where.py`：jdb 驅動腳本（抓 Integrity Bundle、呼叫堆疊）。
