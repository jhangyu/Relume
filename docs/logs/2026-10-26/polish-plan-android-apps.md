# Polish batch, requirement 1 on Android: Followed-project apps in the Apps list

Base: `main` @ 3abe409. `K/` = `android/app/src/main/kotlin/io/github/jhangyu/relume/`. Requirement 2 needs no Android work (preflight Android §2: no Followed projects screen exists on Android).

## API contract (aligned to `polish-plan-be-apps.md` "API contract")

`GET /api/apps` returns `{"apps": [...unchanged...], "followed": [...]}`. Android reads from each followed entry: `id` (project id), `name`, `package` (nullable), `icon_url` (nullable), `version` (downloaded tag, nullable), `blocked` (bool), `files[]` with `name`, `abi` (nullable), `url`. Other keys (`source`, `repo`, `size`, `version_name`, `version_code`, `device_ids`) are ignored (`ApiJson` has `ignoreUnknownKeys = true`, `Models.kt:14`).
Install uses existing routes only: `PUT /api/devices/{id}/assignments` with `project_ids`, then `GET /api/me/plan?projects={id}` (steps of `kind = "project"`, `ref_id` = project id).

## Decisions

1. **Download** = Android `DownloadManager` request to the absolute `files[i].url` with header `Authorization: Bearer <device token>`, saved to the public Downloads folder, system notification on completion. Reason: the APK route needs auth (`auth.py:116`, bearer accepted), so a browser intent would get 401; DownloadManager is the native platform feature, needs no permission on minSdk 29 for `DIRECTORY_DOWNLOADS`, and adds no dependency. Failures appear in the system download notification. The file is the one matching the phone ABI (`Build.SUPPORTED_ABIS` in order, then `universal`, same rule as backend `pick_apk`); no chooser (YAGNI). Hidden when no file matches or the project is blocked.
2. **Install** = the existing plan-based install run (same as built apps), scoped to the project: assign the project to this phone if not assigned, plan `projects=[id]`, then the existing signer / dependency prompts and `InstallRunner`. No direct install path.
3. **Card look**: separate small composable `FollowedCard` reusing `AppTile`, `Txt`, `Hairline`, `ActionButton` (Material3 via `Components.kt:347`) and the phone pill extracted from `PhoneRow`. No status pill, no progress bar, no Log / Add URL / sync / rebuild, no card click (followed apps have no build settings screen).
4. **Badge** (`RelumeRoot.kt:142`): does not count followed apps. It is labelled "enabled" and counts enabled items; followed projects are not enabled items. No edit.
5. **Filters**: followed cards appear under All, Not installed, Phone update (by phone state); never under Failed or Needs URL. Chip counts include them.

## Exact edits

### Step 1. `K/api/Models.kt`
- After `AppObject` (`:332-343`), add:
  ```kotlin
  /** One Followed-project app in `GET /api/apps` `followed`: only Download and Install apply. */
  @Serializable
  data class FollowedApp(
      val id: Int,
      @SerialName("package") val packageName: String?,
      val name: String,
      @SerialName("icon_url") val iconUrl: String?,
      val version: String?,
      val blocked: Boolean,
      val files: List<FollowedFile>,
  ) {
      /** The APK for this phone: first match of the phone ABIs in order, then `universal` (backend `pick_apk` rule); null if none. */
      fun apkFor(abis: List<String>): FollowedFile? =
          abis.firstNotNullOfOrNull { abi -> files.firstOrNull { it.abi == abi } } ?: files.firstOrNull { it.abi == "universal" }
  }

  @Serializable
  data class FollowedFile(val name: String, val abi: String?, val url: String)
  ```
- `:554` `internal data class AppList(val apps: List<AppObject>)` → `data class AppList(val apps: List<AppObject>, val followed: List<FollowedApp>)` (public, no default: a server without the field must fail loudly, not show an empty list).

### Step 2. `K/api/RelumeApi.kt` (two lines only)
- `:24` `private val token: () -> String?` → `val token: () -> String?` (Download needs the bearer token).
- `:77` `suspend fun apps(): List<AppObject> = get<AppList>("/api/apps").apps` → `suspend fun apps(): AppList = get("/api/apps")`.

### Step 3. `K/ui/AppStatus.kt`
- Extract the phone-state rule so built and followed cards share it. Add top-level:
  ```kotlin
  /** Phone state from the plan step; without a step, from the installed package ([newer]: the server has another version). */
  internal fun phoneState(step: PlanStep?, installed: InstalledPackage?, newer: Boolean): PhoneState = when {
      step?.action == PlanAction.INSTALL -> if (installed == null) PhoneState.NotInstalled else PhoneState.Update
      step?.action == PlanAction.ERROR -> PhoneState.Blocked
      step?.action == PlanAction.UP_TO_DATE -> PhoneState.Installed
      installed == null -> PhoneState.NotInstalled
      newer -> PhoneState.Update
      else -> PhoneState.Installed
  }
  ```
- `AppFacts.phone` (`:28-36`) becomes `get() = phoneState(step, installed, app.appVersion != null && app.appVersion != installed?.versionName)`. Behaviour identical (the old `:34` branch only ran with `installed != null`).

### Step 4. `K/ui/AppModel.kt` (regions: `:59-60`, `:95-96`, `:109` area, `:175`, `:196-257`)
- `:59-60`: add next to `apps`: `var followed by mutableStateOf<List<FollowedApp>>(emptyList())` with `private set`.
- `:95-96`: add next to `facts`:
  `fun followedStep(app: FollowedApp): PlanStep? = plan?.steps?.firstOrNull { it.refId == app.id && it.kind == StepKind.PROJECT }`
- `signOutLocally` (`:104-122`): add one line `followed = emptyList()` after `apps = emptyList()`.
- `loadParts` `:175`: `apps = call { it.apps() } ?: return error` →
  `val list = call { it.apps() } ?: return error` / `apps = list.apps` / `followed = list.followed`.
- `beginInstall` (`:213-229`): change signature to `fun beginInstall(kind: StepKind, id: Int)`. Body:
  ```kotlin
  val d = call { it.me().device } ?: return@launch
  val items = d.assignments.map { a -> a.itemId }
  val projects = d.projects.filter { it.assigned }.map { it.projectId }
  // Spec 8.3: assign to this phone first, so OTA keeps it updated too.
  if (kind == StepKind.ITEM && id !in items) call { it.putAssignments(d.id, items + id, null) } ?: return@launch
  if (kind == StepKind.PROJECT && id !in projects) call { it.putAssignments(d.id, items, projects + id) } ?: return@launch
  val scoped = scopedPlan(kind, id) ?: return@launch
  evaluate(kind, id, scoped)
  ```
  plus `private suspend fun scopedPlan(kind: StepKind, id: Int) = call { if (kind == StepKind.ITEM) it.plan(listOf(id), null) else it.plan(null, listOf(id)) }`.
- `InstallPrompt.Signer` (`:198`): `class Signer(val itemId: Int, ...)` → `class Signer(val kind: StepKind, val refId: Int, val plan: InstallPlan, val step: PlanStep)`. `evaluate` (`:231`) takes `(kind, id, scoped)` and builds `Signer(kind, id, scoped, it)`. `acceptSigner` (`:254-255`): `val fresh = scopedPlan(p.kind, p.refId) ?: return@launch` / `evaluate(p.kind, p.refId, fresh)`. (Today the re-plan after accepting a key always plans `items=[id]`, which is wrong for a project; this fixes it for followed installs.) `InstallScreen.kt:278` uses only `p` and needs no edit.
- New function after `acceptSigner`:
  ```kotlin
  /** Download: the system DownloadManager saves the followed APK to Downloads; it shows progress and failures itself. */
  fun download(app: FollowedApp, file: FollowedFile) {
      val a = checkNotNull(api) { "download of ${app.name} without a signed-in client" }
      val token = checkNotNull(a.token()) { "download of ${app.name} without a device token" }
      val request = DownloadManager.Request(Uri.parse(a.url.resolve(file.url).toString()))
          .addRequestHeader("Authorization", "Bearer $token")
          .setTitle(app.name)
          .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
          .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, file.name)
      relume.getSystemService(DownloadManager::class.java).enqueue(request)
  }
  ```
  Imports: `android.app.DownloadManager`, `android.net.Uri`, `android.os.Environment`, `FollowedApp`, `FollowedFile`.

### Step 5. `K/ui/AppsScreen.kt`
- `AppFilter` (`:39-45`): add a second predicate `val followed: (PhoneState) -> Boolean`: All `{ true }`, NotInstalled `{ it == PhoneState.NotInstalled }`, PhoneUpdate `{ it == PhoneState.Update }`, Failed `{ false }`, NeedsUrl `{ false }`.
- `AppsScreen` (`:52-56`): add
  `val followed = model.followed.map { it to phoneState(model.followedStep(it), it.packageName?.let(model.installed::get), newer = false) }`,
  `val visibleFollowed = followed.filter { (_, p) -> AppFilter.entries[filter].followed(p) }`,
  and a second set `installingProjects` from the same `run` steps with `StepKind.PROJECT`.
- Chip labels `:64`: counts add followed: `"All ${all.size + followed.size}"`, `"${f.title} ${all.count(f.matches) + followed.count { f.followed(it.second) }}"`.
- LazyColumn `:71-73`: after the built `items(...)`, add `items(visibleFollowed, key = { "followed-${it.first.id}" }) { (app, phone) -> FollowedCard(app, phone, app.id in installingProjects, model) }`.
- Empty text `:74-77`: `visible.isEmpty() && visibleFollowed.isEmpty()`; `all.isEmpty() && followed.isEmpty()`.
- `:187` and `:189`: `model.beginInstall(f)` → `model.beginInstall(StepKind.ITEM, f.app.id)`.
- `PhoneRow` (`:166-193`): move the pill `when` (`:176-182`) into `@Composable private fun PhonePill(installing: Boolean, phone: PhoneState, target: String?, installed: InstalledPackage?)`; `PhoneRow` calls `PhonePill(installing, f.phone, f.target, f.installed)`.
- New composable after `PhoneRow`:
  ```kotlin
  /** Followed-project app: the head and the phone row with only Download and Install (spec req 1). */
  @Composable
  private fun FollowedCard(app: FollowedApp, phone: PhoneState, installing: Boolean, model: AppModel) {
      val apk = app.apkFor(Build.SUPPORTED_ABIS.toList())
      Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerLowest) {
          Column(Modifier.padding(top = 16.dp)) {
              Row(Modifier.padding(horizontal = 16.dp).padding(bottom = 12.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                  AppTile(app.name, app.iconUrl, model.api, category = null)
                  Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                      Txt(app.name, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                      Txt(listOfNotNull(app.version, "Followed project").joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = muted(), maxLines = 1)
                  }
              }
              Hairline()
              Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                  PhonePill(installing, phone, app.version, app.packageName?.let(model.installed::get))
                  Spacer(Modifier.weight(1f))
                  if (apk != null) ActionButton("Download", { model.download(app, apk) }, kind = ButtonKind.Text, size = ButtonSize.Small)
                  if (!installing && !app.blocked && app.files.isNotEmpty() && (phone == PhoneState.NotInstalled || phone == PhoneState.Update)) {
                      ActionButton(if (phone == PhoneState.Update) "Update" else "Install", { model.beginInstall(StepKind.PROJECT, app.id) }, kind = ButtonKind.Install, icon = R.drawable.ms_install_mobile, size = ButtonSize.Small)
                  }
              }
          }
      }
  }
  ```
  Import `android.os.Build`. `AppTile` has `category: String? = null` (`Components.kt:768`). Blocked projects have empty `files`, so both buttons hide; the phone pill stays.

No other files. `RelumeRoot.kt` badge: no edit (Decision 4).

## Files touched

`K/api/Models.kt`, `K/api/RelumeApi.kt`, `K/ui/AppStatus.kt`, `K/ui/AppModel.kt`, `K/ui/AppsScreen.kt`. Tests: none change (see below).

## Shared files with the Server URL implementer (req 8)

- `RelumeApi.kt`: this plan edits only line `:24` (constructor `token` visibility) and `:77` (`apps()`). The Server URL work should not touch these two lines.
- `AppModel.kt`: this plan owns `:59-60` (new `followed` state), `:95-96` (`followedStep`), one line inside `signOutLocally` after `apps = emptyList()`, `loadParts :175`, and `:196-257` (InstallPrompt, beginInstall, evaluate, acceptSigner, new `download`). The Server URL work should add its function outside these regions (e.g. next to `logout`, `:124-133`) and call `signOutLocally` without editing it. If both need `signOutLocally`, the lead serialises the two edits.
- `SettingsScreen.kt`: not touched by this plan.

## Tests

- No new tests. No UI-type tests exist on Android (preflight §5), so none to delete.
- `ui/InstallStateTest` (`:20` builds `AppObject`, `:33+` checks `AppFacts.phone`): `AppObject` is unchanged and `AppFacts.phone` behaviour is unchanged, so it must compile and pass as is.
- Grep check: `grep -rn "AppList(\|\.apps()\|beginInstall(\|InstallPrompt.Signer(" android/app/src` must show only the call sites edited above (test sources must not construct `AppList` or `Signer`; preflight found `PlanParseTest`/`MarketParseTest` do not).

## Acceptance (mechanical)

1. `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` RC=0.
2. `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:assembleDebug --console=plain --no-watch-fs` RC=0.
3. `grep -n "FollowedCard" K/ui/AppsScreen.kt` shows the composable and its one call; inside `FollowedCard` there is no `ms_sync`, `ms_build`, `onOpenLog`, `StatusPill`, `LinearProgressIndicator` or `clickable`.
4. `grep -n "followed" K/ui/RelumeRoot.kt` returns nothing (badge unchanged).
5. Live check on the phone is the user's (spec: no final UI review by the team).

## Open items

- Does `GET /api/me/plan?projects={id}` return a step for a project just assigned to an Android companion device, before its first sync? (Backend preflight A2: companion devices get projects through the plan, `ota.py:297-311`; not verified for the "just assigned" case. The backend plan's decision 4 covers this route.)
- DownloadManager with an `http://` private-address server: Android's download provider applies the calling app's cleartext policy, which allows cleartext here (`res/xml/network_security_config.xml`). Not verified on device.

---

# Requirement 9 on Android: Marketplace auto-load

**Result: already done on `main`. Android needs no code edit.** Commit 4928572 added auto-load, and commit 374030d kept it.

What the code does today (`K/ui/` = `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/`):
- **Trigger:** `MarketplaceScreen.kt:124-130` adds a "more" item after the last app while `list.hasMore`. When this item becomes visible (composed), `LaunchedEffect(list) { model.loadMore() }` loads the next page. The item shows "Loading more…".
- **No double loads:** `MarketModel.kt:178-186` `loadMore` does nothing while `listJob` is active, whether a page-1 load or a page request runs. The `LaunchedEffect` key is the list, so it runs once per loaded list.
- **Stops at the last page:** `hasMore = page * perPage < total` (`MarketModel.kt:47`). The item and `nextMarketPage` (`:50-54`) both check it.
- **Duplicates removed:** `nextMarketPage` removes duplicate apps with `distinctBy { packageName }`.
- **First-load Retry banner (374030d):** `MarketplaceScreen.kt:85-88`, `listLoad.error` with Retry → `loadAll`. The pager does not change it.
- **No "Load more" button exists:** `grep -rni "load more" android/app/src` finds only the comment and the call at `:127`.

Known limit (kept, YAGNI): when a next-page request fails, the "Request failed" banner shows the error. "Loading more…" stays until the user scrolls the item out of view and back, which composes it again and retries. Change this only if the user asks.

Files touched for req 9: none. Shared regions: none.
Acceptance:
1. `grep -rni "load more" android/app/src --include=*.kt` shows no button. Only the comment and `LaunchedEffect` at `MarketplaceScreen.kt:125-127` remain.
2. The Android leg is green. It is covered by the requirement 1 run.
