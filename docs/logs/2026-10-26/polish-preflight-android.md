# Polish batch: Android preflight (read-only)

Base: `main` @ 3abe409. Paths below are under `android/app/src/main/kotlin/io/github/jhangyu/relume/` (`K/`). Spec reqs 1, 2, 8.

## 1. Apps list (req 1)
- Screen: `K/ui/AppsScreen.kt:48` `AppsScreen`; one card per app `AppCard` `:85`; bottom install row `PhoneRow` `:158`.
- Filter chips: `AppFilter` enum `:37-44` (All, Not installed, Phone update, Failed, Needs URL), counts over `model.apps.map(model::facts)` `:52-53`.
- View model: `K/ui/AppModel.kt`. `apps` state `:59`; loaded in `loadParts` `:173-184` via `call { it.apps() }` `:175`. `facts(app)` `:95` builds `AppFacts` (`K/ui/AppStatus.kt:22`) from installed packages and the install plan.
- API: `K/api/RelumeApi.kt:77` `apps()` = `GET /api/apps`, decoded as `AppList`; model `AppObject` `K/api/Models.kt:332-343` (id, package, name, icon_url, bundle{bundle_id,name,origin,engine,tag}, app_version, status, download, category). No source/follow marker field exists yet.
- Per-app actions on the card today:
  - Text button "Log"/"Live log" `AppsScreen.kt:141-143` (when job running/failed); "Add URL"/"Pick one" `:144-148` (lookup fix).
  - Icon buttons: Check for updates (sync) `:150`, Rebuild/Retry (build) `:152-157`.
  - Card click opens app settings `:106` (`onOpenSettings`).
  - Install / Update (Button, kind Install) `:186-190`, only when `f.installable` (plan step) and phone state NotInstalled/Update.
  - No per-app schedule, patch or download-APK button on Android. "Check all" is a top bar sync icon `:60` (`model::runSchedule`).
- Followed-apps with only Download + Install, what it takes:
  1. `Models.kt` `AppObject`: add the server's source marker field (name decided by the backend; default so old JSON still parses).
  2. `AppsScreen.kt` `AppCard`: when marker = followed, skip the status-line row actions (Log, Add URL/Pick one, sync, rebuild `:140-157`), the progress bar and the status pill; card click must not open `AppSettingsScreen` (`:106`, followed apps have no build settings).
  3. A "Download" action does not exist on Android at all. Install downloads via `InstallRunner`/`Downloader` (`K/install/Downloader.kt`, `InstallWorker.kt`), which is not a user-facing "save the APK" action. Download needs a new button; the likely implementation is opening the server APK URL (`DownloadDecision.kt` / `Downloader.kt` have the URL rules, redirects not followed). Needs an architect decision.
  4. Install path: `AppModel.beginInstall` `:213-229` calls `putAssignments` (adds the app id to the device assignments) then `plan(items=[id])`. Must be checked against what the backend does for a followed-project app id (it may not be an "item" id). `AppFacts.phone`/`installable` come from the plan step; for followed apps the plan must contain the step, else the Install button will not show.
  5. Filters `AppFilter` `:37-44`, `AppFacts.lookup`/`building` are written for built apps; check followed apps do not land in "Needs URL"/"Failed".
  6. NavBar badge `RelumeRoot.kt:142` counts `model.apps.size` ("enabled" label `:165`); decide whether followed apps count.
  7. Empty text "No apps enabled. Enable one from the Marketplace." `AppsScreen.kt:75`.

## 2. Followed projects screen (req 2)
- Does not exist on Android. `grep -i follow` over all `K/` finds only: "Depends on Followed projects" section header in `K/ui/AppSettingsScreen.kt:240-242`, the job-log "Follow" switch `JobScreens.kt:90,144`, and comments. `Screen.kt` has no projects screen. No "Install to phone"/"Check now" buttons exist on Android. Per spec ("If Android has the same buttons"), req 2 needs no Android work.
- Android already uses Material3 buttons for everything via one wrapper `ActionButton` (`K/ui/Components.kt:347-390`): Button / OutlinedButton / TextButton. Install kind = tertiary colours.

## 3. Hand-made buttons / icons (req 2 side check)
- Icons: all are bundled vector drawables `res/drawable/ms_*.xml` (30 files, Material Symbols exports) drawn with `painterResource` (`Components.kt:163,179`, `RelumeRoot.kt:163`). No custom `ImageVector`, no Canvas/Path code. `build.gradle.kts` dependencies `:96-105`: compose-bom, ui, foundation, material3 only. `material-icons-extended` and `material-icons-core` are NOT present.
- Buttons: `Tonal` kind is a `Button` with secondary container colours (`Components.kt:380`, not `FilledTonalButton`). Everything else is Material3. Custom parts: `Pill`, `StatusPill`, `ChipRow` (Components.kt) are display/choice widgets, not buttons.
- Hand-made clickables (Surface/Box + `clickable`), not Material buttons: AppCard (`AppsScreen.kt:105-106`), Marketplace rows (`MarketplaceScreen.kt:170,219`), `Components.kt:427` (banner action), `:478` (account avatar), `LoginScreen.kt:176` (adopt-device row). These are list rows/cards, so likely fine; the banner action `:427` (clickable text with `padding(top=12.dp)`) is the only button-like one.
- Mixed small-button sizing: Small height 32 dp / padding 14 dp (`Components.kt:335-337`), Large 48 dp / 24 dp, Medium default Material3.

## 4. Server URL (req 8)
- First entry: `K/ui/LoginScreen.kt` ("Connect to Relume"), field `:157-160`; prefilled from `app.prefs.serverUrl` `:71`. Parsed with `ServerUrl.parse` `:100` (`K/api/ServerUrl.kt:34-58`, http only for private hosts, no path/query/userinfo). Login `submit()` `:95-136` calls `RelumeApi(url){null}.health()` `:108-110` (unreachable -> banner with Retry `:112,155`) then `registerDevice`.
- Storage: `K/api/Prefs.kt:12-14` SharedPreferences file `relume`, key `server_url`, string, re-parsed on read; `commit()` and `check` on failure `:25-28`. Not DataStore.
- Token: `K/api/TokenStore.kt` AES-256-GCM, Keystore key alias `relume-device-token`, SharedPreferences file `relume-token` keys `ciphertext`, `iv`. `clear()` `:40` clears that whole file. No cookies are used (bearer token).
- HTTP client: `RelumeApi(val url: ServerUrl, token: () -> String?)` `RelumeApi.kt:24`; URL is fixed per instance, token read per request. The one live instance is `RelumeApp.api` (`RelumeApp.kt:17`), built at startup `:25` if url and token exist, or in `signIn` `:29-33`. `AppModel.api` is a Compose state copy `AppModel.kt:53`. Other `RelumeApi(` constructions: `LoginScreen.kt:84,108` only. Workers (CheckWorker, InstallWorker) get the api from `RelumeApp.api` (grep for `prefs.serverUrl` finds only RelumeApp.kt:24,30 and LoginScreen/Settings display).
- Sign-out: `AppModel.signOutLocally(reason)` `:104-122` -> `RelumeApp.signOut()` `RelumeApp.kt:36-39` (token cleared, `api = null`, server URL kept) + resets all model state and cancels the check schedule. `logout()` `:124` first calls server `/api/me/logout`, then always signs out locally. With `api == null`, `RelumeRoot.kt:102-109` shows `LoginScreen` (no explicit navigation needed; the back stack is kept, but `signedIn()` runs on login).
- Settings screen: `K/ui/SettingsScreen.kt`. Sections: Server `:44-56` (a read-only "Server URL" `ListRow` with supporting text = `prefs.serverUrl` `:46`, "Connection" pill from `model.health` `:48-55`), Account `:58-63` (Sign out and forget token), Background, Storage, This device, About. Plain row, no click handler yet. Dialogs use `RDialog` + `DialogButtons` (`Components.kt:739`; example `SettingsScreen.kt:109-121`) and `Field` (`Components.kt:~700`, used in `AppsScreen.kt:UrlDialog`).
- Changing the URL at runtime needs (all in existing pieces): `ServerUrl.parse(text)` -> `RelumeApi(newUrl){null}.health()` (add a compat check) -> on success `prefs.serverUrl = new` and sign out so the login screen shows (`model.signOutLocally(...)` calls `relume.signOut()`, which clears the token; the prefs write must be added, as `signOut` keeps the URL). `RelumeApp` has no setter to change url without a token, but `prefs.serverUrl = ...` is a public var (`SettingsScreen` already holds `model.relume.prefs`). Login prefill reads `prefs.serverUrl` at `LoginScreen.kt:71` (remembered with `rememberSaveable`, so a stale value is possible only within the same composition; Login is a fresh composition after sign-out).
- `/api/health`: exists. `RelumeApi.kt:26` `health()` -> `Health(ok, version)` (`Models.kt:229-232`). Callers: login reachability `LoginScreen.kt:110`; every refresh `AppModel.kt:174` (shown in Settings "Connection"). NO version compatibility check anywhere: `version` is only displayed (`SettingsScreen.kt:52`), `ok` is never tested, and there is no min/max server version or app-version constant. The spec's "compatible" needs a rule defined (e.g., `ok == true` and parse of `version`, or a server field). Also note `get()` throws `ApiError` for HTTP errors and `IOException` for network (Login catches only IOException around health).

## 5. Existing tests (`android/app/src/test`; no `androidTest` dir)
All JUnit4 unit tests, no Compose/UI tests exist.
- Logic: `api/ServerUrlTest` (URL parse rules), `api/RedirectTest`, `api/PlanParseTest`, `api/MarketParseTest` (JSON parse), `api/SseParserTest`, `install/ApkVerifierTest`, `install/DownloadDecisionTest`, `install/InstallPersistenceTest`, `install/InstallResultTest`, `install/InstallRunTest`, `sync/BackgroundStartTest`, `ui/InstallStateTest` (AppFacts phone state/lookup decisions, model logic), `ui/MarketFilterTest` (paging/featured/error state of MarketModel), `ui/ScheduleSpecTest` (schedule spec building logic). `install/Fixtures.kt` is a helper.
- UI-type by the spec's definition (style/hue): `ui/CategoryHueTest` (category name to colour hue: presentation mapping, not layout). Judgment: it is a visual mapping test, candidate for deletion only if the batch touches category colours (it does not).
- Tests touching areas this batch changes: `ServerUrlTest` (keep, unchanged logic), `InstallStateTest` (`AppFacts`/`AppObject` constructor: adding a field with a default keeps it compiling; a field without default breaks `app()` helper `:20`). `PlanParseTest`/`MarketParseTest` build JSON for other models, not `AppObject` (to be confirmed by an implementer when the field is added).
- No layout, composable-render or button-wiring tests exist, so none to delete for Android.

## 6. Build commands check
- Handover §1 step 4: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:assembleDebug --console=plain --no-watch-fs`. Matches: `android/gradlew` and `android/settings.gradle.kts` exist; `:app` module; `local.properties` has `sdk.dir`. `build.gradle.kts:17` comment says APKs are named `relume-<buildType>.apk`, so `relume-debug.apk` under `app/build/outputs/apk/debug/` is consistent. Only a `release` build type is declared (`:63-67`); `debug` is the implicit default with the default debug keystore. Not run (read-only). Install command (`adb -s 10AFCF269C007FB install -r ...`) not verifiable here.

## Uncertain
- Whether the backend's followed-app ids are valid for `plan(items=[id])` / `putAssignments` (backend preflight owns this).
- The Download action's target (APK URL) on Android has no existing code; needs design.
- "Compatible" rule for `/api/health` is undefined in code and spec.
- `CategoryHueTest` classification is a judgment call.
