# Apps install states, install lightbox, reinstall, daily check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use podium:team-spawn (recommended) or podium:team-fable to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Apps card (catalog item and followed project) shows exactly one of Install / Uninstall & reinstall / Upgrade / "Installed <version>", computed from the built APK's package; installs run in a bottom-sheet lightbox over the Apps tab; a signer mismatch runs confirm → system uninstall → automatic install; the background check gains "Daily at <time>".

**Architecture:** The server adds the built APK's facts (package, version code/name, patch version, signer) to each `GET /api/apps` item (`build`), the patch version to item plan downloads, and the signer to followed projects. On the phone one pure function `cardAction(target, installed, installedPatch)` in `ui/AppStatus.kt` decides the card action for both card kinds; `install/InstalledPatches.kt` records the patch version when Relume installs a package. The full-page `Screen.Install` is deleted and replaced by `InstallSheet` (Material3 `ModalBottomSheet`) driven by `AppModel.sheetOpen`; the reinstall chain reuses the existing `InstallRunner.uninstallAndRetry` and gets a terminal state when the user cancels the system uninstall. `CheckScheduler` gains a 24 h periodic request with an initial delay to the chosen time and charging + unmetered constraints.

**Tech Stack:** Python 3 stdlib server (`local/webui`, unittest), Kotlin + Jetpack Compose (compose-bom 2026.09.00 → material3 1.4.0), androidx.work 2.12.0, kotlinx-serialization-json 1.12.0, JUnit 4.

**Decisions (hard-to-reverse only):**
- Card target for catalog items = server `build` facts of the artifact `build.latest_artifact(item_id, first ABI of the calling phone)` (the same artifact the plan installs). Alternatives: (a) the plan step's download (only exists for assigned apps with action install), (b) any artifact of the newest job (version code can differ per ABI). Driver: one source that exists for every built app and matches the plan. Drawback: the web session (no device) gets the `universal` artifact or `null`; the web UI does not read `build`.
- Followed-project target = the plan step download when the plan has one, else the first listed file plus the project signer. Driver: project files differ per ABI, the plan already picked the phone's ABI. Drawback: an unassigned project compares against the first file.
- Patch version on the phone is a local SharedPreferences record (`relume-patches`, package → patch version), written only when a Relume install step ends `installed`. No record → the patch version is not compared (the app version and signer still are). Driver: the phone cannot read the patch version from an installed APK. Drawback: apps installed before this change or by OTA/ADB never show Upgrade for a patch-only change until Relume installs them once.
- Cancelling the system uninstall ends the step `cancelled` (new transition `InstallRun.endUninstall`); any other uninstall failure ends it `failed`. Before, the step stayed `needs_uninstall` with a note. Driver: the lightbox needs a terminal state to close (cancel) or show the error (failure). Drawback: none known; the step's failure receipt was already due when it became `needs_uninstall`, so no extra receipt is sent.
- `CheckScheduler.apply` uses `ExistingPeriodicWorkPolicy.CANCEL_AND_REENQUEUE` for every interval (was `UPDATE`). Driver: a new daily time must take effect with a new initial delay. Drawback: changing the interval or signing in restarts the period.

Plan gate: `${CLAUDE_PLUGIN_ROOT}/protocols/review.md` §L0 Plan gate.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-10-28-apps-install-states-design.md`; visual design: `docs/mockups/install-lightbox-b.html` (bottom sheet for progress, failure and the reinstall confirm).
- Card states in order: not installed → **Install**; installed and signing cert differs from the server build → **Uninstall & reinstall**; installed and app version older OR patch version older → **Upgrade**; otherwise "Installed <version>" pill. A build in progress still hides the action.
- Every phone-side comparison (installed lookup, version, patch version, signer) uses the package name of the built APK, never the source app's package (`AppObject.packageName`).
- Lightbox: success closes it and the cards refresh; failure keeps it open with the error and Retry / Close.
- Reinstall confirm text states that the app's data and login are erased. Confirm → system uninstall prompt → install starts automatically → back to Apps. Cancel of the system prompt stops the flow, card unchanged.
- Daily check: "Daily at <time>" next to 1 h / 6 h / 24 h, user-picked time, default 03:15; WorkManager periodic 24 h with initial delay to the next chosen time; constraints charging + unmetered network; approximate time, no exact alarms (`SCHEDULE_EXACT_ALARM` is out of scope). Background runs keep the existing silent update (`USER_ACTION_NOT_REQUIRED`).
- Out of scope: server push (FCM / UnifiedPush), exact-time alarms.
- Tests: only the action-function unit test (Kotlin) and one server test for the new fields (package identity is data-integrity logic). Lightbox, reinstall chain and daily check are verified by one real device run each, not committed as tests. No UI, screenshot or e2e tests. Per test ≤ 1 s, full suite ≤ 90 s.
- Test entry: `python3 scripts/test.py` (legs `python`, `shell`, `android`); Android leg needs `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools`.
- No backward compatibility: removed code (`PhoneState`, `phoneState`, `AppFacts.installable`, `AppFacts.phone`, `InstallScreen`, `UninstallDialog`, `Screen.Install`, `AppModel.runStarted`, `AppModel.followedPhone`, `InstallRun.note`) is deleted with its tests, no shims.
- Error rules: no swallowed exceptions, no silent fallback; `check`/`require` messages name expected value, actual value and the affected object.
- Git in the shared tree: no `git stash` (incl. `stash list`), `reset`, `checkout --`, `clean`, `git add -A`. Commit with `git commit -m "<msg>" -- <own paths>`; no `;` inside `-m`. `git mv` lists both paths in the commit pathspec. Never open `local/data/webui.db` with sqlite3 from the host.

## Parallel groups and file ownership

| Group | Tasks (run in parallel inside a group) | Starts after |
|---|---|---|
| A | Task 1 (server), Task 2 (models + action function), Task 6 (daily check) | — |
| B | Task 3 (patch record + uninstall end) | Task 2 committed (uses `Download.patchVersion`) |
| C | Task 4 (Apps cards + AppModel + reinstall sheet) | Tasks 2 and 3 committed |
| D | Task 5 (install lightbox) | Task 4 committed (uses `AppModel.sheetOpen`, `RSheet`, `ReinstallSheet`) |
| E | Task 7 (gates + device run) | Tasks 1–6 committed |

Ownership (no file is in two tasks of the same group):
- Task 1: `local/webui/apps.py`, `local/webui/market.py`, `local/webui/ota.py`, `local/webui/projects.py`, `local/webui/tests/test_device_app.py`
- Task 2: `android/app/src/main/kotlin/io/github/jhangyu/relume/api/Models.kt`, `.../ui/AppStatus.kt`, `android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt`
- Task 3: `.../install/InstalledPatches.kt` (new), `.../install/InstallRunner.kt`, `.../install/InstallRun.kt`
- Task 4: `.../ui/AppStatus.kt`, `.../ui/AppModel.kt`, `.../ui/AppsScreen.kt`, `.../ui/ReinstallSheet.kt` (new), `android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt`
- Task 5: `.../ui/InstallScreen.kt` → `.../ui/InstallSheet.kt` (git mv), `.../ui/RelumeRoot.kt`, `.../ui/Screen.kt`, `.../ui/AppModel.kt` (only the `runStarted` lines; Task 4 is done by then)
- Task 6: `.../api/Prefs.kt`, `.../sync/CheckScheduler.kt`, `.../sync/CheckWorker.kt`, `.../ui/SettingsScreen.kt`
- Task 7: `docs/logs/2026-10-28-install-states-device-run.md` (new, scratch-style result page)

`...` = `android/app/src/main/kotlin/io/github/jhangyu/relume`.

Root causes this plan fixes (verified in code on 1a72956):
- `ui/AppStatus.kt:69-71` `AppFacts.installable` needs a plan step with action `INSTALL`, or no step plus `appVersion`; a step with action `ERROR` or `UP_TO_DATE` drops the button.
- `ui/AppsScreen.kt:226` followed cards use a second, separate rule.
- `ui/AppModel.kt:98-99` and `ui/AppsScreen.kt:224` look up the installed package by the source package (`app.packageName`), while the server plan uses the artifact package (`local/webui/ota.py:128-131`, `pkg = step['package'] = a['package']`).

---

### Task 1: Server exposes the built APK facts (group A)

**Files:**
- Modify: `local/webui/apps.py:83-86` (`list_enabled`) and add `_build_facts` above it
- Modify: `local/webui/market.py:1` (imports) and `:109-111` (`list_apps`)
- Modify: `local/webui/ota.py:258-261` (`_item_download`)
- Modify: `local/webui/projects.py:271-288` (`followed_apps`)
- Test: `local/webui/tests/test_device_app.py` (new method in `AppTest`)

**Interfaces:**
- Consumes: `build.latest_artifact(item_id, abi) -> dict | None` (artifacts row: `package`, `version_code`, `version_name`, `patches_version`, `signer_sha256`), `ota.first_abi(dev) -> str`, `devices.get(device_id) -> dict`.
- Produces (JSON contract the phone parses, Task 2):
  - `GET /api/apps` → each `apps[i]` gains `"build": null | {"package": str, "version_code": int|null, "version_name": str|null, "patch_version": str|null, "signer_sha256": str|null}`. `null` when the item has no success build or its artifact has no package.
  - Plan item step `download` gains `"patch_version": str|null`.
  - `GET /api/apps` → each `followed[i]` gains `"signer_sha256": str|null` (signer of the downloaded release, `null` when nothing is downloaded); its `files[i].version_code` already exists.
  - Python: `apps.list_enabled(abi: str = 'universal') -> list[dict]`.

**Behavior:**
- `list_apps` called with a device token uses the artifact for that phone's first ABI (`ota.first_abi(devices.get(req.device_id))`), the same choice the plan makes (`ota.py:161`); a web session uses `'universal'`.
- An artifact with an empty or null `package` gives `build: null` (the plan already turns it into an `ARTIFACT_INCOMPLETE` error step); never fall back to `item.package`.
- `apps.get()` (single app detail) is unchanged: `build` is only in the list.

**Constraints:**
- stdlib only; no new module-level state.
- `market.py` may import `devices` and `ota` (neither imports `market`; verify with the import check in step 4).

**Acceptance criteria:**
- [ ] `webui.tests.test_device_app.AppTest.test_apps_list_and_plan_carry_built_package_patch_and_signer` exists and passes.
- [ ] `python3 scripts/test.py --legs python` exits 0.
- [ ] `grep -n "'patch_version'" local/webui/apps.py local/webui/ota.py` prints one line in each file.

**Steps:**

- [ ] **Step 1: Write the failing test.** In `local/webui/tests/test_device_app.py` change the import line `from webui import build, db, ota, projects` to `from webui import build, db, market, ota, projects`, then add this method to `class AppTest` (after `test_renamed_package_uses_artifact_package`):

```python
    def test_apps_list_and_plan_carry_built_package_patch_and_signer(self):
        """Install states compare the built APK (spec install-states 1): renamed package, patch version, signer."""
        renamed = 'app.morphe.android.youtube'
        assign(self.me, 1)
        jid = add_job(kind='build', target={'item_id': 1}, status='success')
        with db.tx() as c:
            c.execute("INSERT INTO artifacts(job_id,item_id,variant,abi,package,version_name,version_code,patches_version,"
                      "signer_sha256,path,size,sha256,created_at) VALUES (?,1,'universal','universal',?,'20.1',7,'v1.25.2',?,"
                      "'/a/x.apk',3,?,'now')", (jid, renamed, SIGNER, 'cd' * 32))
        st, body = self.call(market.list_apps)
        self.assertEqual(st, 200)
        self.assertEqual(body['apps'][0]['build'], {'package': renamed, 'version_code': 7, 'version_name': '20.1',
                                                    'patch_version': 'v1.25.2', 'signer_sha256': SIGNER})
        step = self.call(ota.me_plan)[1]['plan']['steps'][0]
        self.assertEqual((step['package'], step['download']['patch_version']), (renamed, 'v1.25.2'))
        with db.tx() as c:
            c.execute("UPDATE artifacts SET package = '' WHERE job_id = ?", (jid,))
        self.assertIsNone(self.call(market.list_apps)[1]['apps'][0]['build'])   # never guessed from item.package
```

- [ ] **Step 2: Run it to see it fail.**

Run: `cd local && PYTHONPATH=. python3 -m unittest webui.tests.test_device_app.AppTest.test_apps_list_and_plan_carry_built_package_patch_and_signer -v`
Expected: FAIL / ERROR with `KeyError: 'build'`.

- [ ] **Step 3: Server code.**

`local/webui/apps.py` — replace `list_enabled` (lines 83-86) with:

```python
def _build_facts(item_id, abi):
    """The built APK a phone of this ABI would install (spec install-states 1): its package can differ from
    item.package (package-rename patch). None when there is no success build or its artifact has no package."""
    a = build.latest_artifact(item_id, abi)
    if a is None or not a['package']:
        return None
    return {'package': a['package'], 'version_code': a['version_code'], 'version_name': a['version_name'],
            'patch_version': a['patches_version'], 'signer_sha256': a['signer_sha256']}


def list_enabled(abi='universal'):
    rows = db.conn().execute(ROW_SQL + ' WHERE i.enabled = 1 ORDER BY i.id').fetchall()
    status = build.item_status([r['id'] for r in rows])
    return [dict(_app_obj(r, status[r['id']]), build=_build_facts(r['id'], abi)) for r in rows]
```

`local/webui/market.py` — line 3 becomes `from . import apklocate, apps, build, catalog, db, devices, ota, projects`, and `list_apps` becomes:

```python
def list_apps(req):
    # A phone gets the artifact of its first ABI, the one its plan installs (ota._plan_for).
    abi = ota.first_abi(devices.get(req.device_id)) if req.device_id is not None else 'universal'
    return 200, {"apps": [_with_download(a) for a in apps.list_enabled(abi)],
                 "followed": projects.followed_apps()}
```

`local/webui/ota.py` — `_item_download` becomes:

```python
def _item_download(a):
    return {'url': f"/api/artifacts/{a['id']}/download", 'file_name': os.path.basename(a['path']), 'size': a['size'],
            'sha256': a['sha256'], 'signer_sha256': a['signer_sha256'], 'version_code': a['version_code'],
            'version_name': a['version_name'], 'artifact_id': a['id'], 'patch_version': a['patches_version']}
```

`local/webui/projects.py` — in `followed_apps`, add the signer to the dict after `"blocked": blocked,`:

```python
                    "signer_sha256": facts[0].get("signer_sha256") if facts else None,
```

- [ ] **Step 4: Run the test and the import check.**

Run: `cd local && PYTHONPATH=. python3 -c "import webui.market, webui.auth" && PYTHONPATH=. python3 -m unittest webui.tests.test_device_app -v`
Expected: no import error; all `test_device_app` tests PASS, including the new one.

- [ ] **Step 5: Python leg.**

Run: `python3 scripts/test.py --legs python; echo RC=$?`
Expected: `RC=0`. If an existing test compares a whole plan `download` dict or a whole `GET /api/apps` entry, add `'patch_version'` / `'build'` / `'signer_sha256'` to its expected value (do not loosen the assertion).

- [ ] **Step 6: Commit.**

```bash
git rev-parse --abbrev-ref HEAD
git add local/webui/apps.py local/webui/market.py local/webui/ota.py local/webui/projects.py local/webui/tests/test_device_app.py
git commit -m "feat(server): apps list and plan carry the built APK package, patch version and signer" -- local/webui/apps.py local/webui/market.py local/webui/ota.py local/webui/projects.py local/webui/tests/test_device_app.py
git status --porcelain -- local/webui
```

### Task 2: Phone models and the card action function (group A)

**Files:**
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/api/Models.kt` (`Download`, `AppObject`, `FollowedApp`, `FollowedFile`, new `AppBuild`)
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt` (add `CardAction`, `InstallTarget`, `cardAction`, `versionOlder`, `AppObject.target`, `FollowedApp.target`; existing code untouched in this task)
- Test: `android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt` (replace the two phone-state tests)

**Interfaces:**
- Consumes: Task 1 JSON contract (fields above). `ApiJson` has `ignoreUnknownKeys = true`, so every new field has a default.
- Produces:
  - `Download.patchVersion: String?` (`@SerialName("patch_version")`, default `null`)
  - `@Serializable data class AppBuild(packageName: String, versionCode: Long?, versionName: String?, patchVersion: String?, signerSha256: String?)` with serial names `package`, `version_code`, `version_name`, `patch_version`, `signer_sha256`
  - `AppObject.build: AppBuild?` (default `null`)
  - `FollowedApp.signerSha256: String?` (`signer_sha256`, default `null`); `FollowedFile.versionCode: Long?` (`version_code`, default `null`)
  - `enum class CardAction { Install, Reinstall, Upgrade, Installed }`
  - `data class InstallTarget(val packageName: String, val versionCode: Long?, val versionName: String?, val patchVersion: String?, val signerSha256: String?)`
  - `internal fun cardAction(target: InstallTarget, installed: InstalledPackage?, installedPatch: String?): CardAction`
  - `internal fun versionOlder(a: String, b: String): Boolean`
  - `internal val AppObject.target: InstallTarget?`
  - `internal fun FollowedApp.target(step: PlanStep?): InstallTarget?`

**Behavior:**
- `cardAction` checks in order: `installed == null` → `Install`; both signers known and different (case-insensitive hex) → `Reinstall`; `installed.versionCode < target.versionCode` → `Upgrade`; recorded patch and target patch both known and `versionOlder(installedPatch, target.patchVersion)` → `Upgrade`; else `Installed`. Unknown target fields (null) skip their comparison.
- `versionOlder` orders like the server's `catalog.version_key` (`local/webui/catalog.py:104-109`): drop one leading `v`/`V`, split on `.`, `-`, `+`, `_`; an all-digit part is its number, any other part is −1; append 0; compare lexicographically, a shorter prefix is smaller.
- `AppObject.target` = `build` mapped to `InstallTarget`, `null` when `build` is null.
- `FollowedApp.target(step)`: when `step?.download` is not null → `InstallTarget(step.packageName, d.versionCode, d.versionName, d.patchVersion, d.signerSha256)`; else when `packageName != null && !blocked && files.isNotEmpty()` → `InstallTarget(packageName, files[0].versionCode, files[0].versionName, null, signerSha256)`; else `null`.

**Constraints:**
- Pure Kotlin in `ui/AppStatus.kt`, no Android imports.
- Do not change `AppFacts`, `PhoneState` or `phoneState` here (Task 4 replaces them; changing them now breaks `AppModel`/`AppsScreen` compilation).

**Acceptance criteria:**
- [ ] `InstallStateTest.cardActionFollowsTheFourConditionsInOrder`, `InstallStateTest.patchVersionComparesLikeTheServer`, `InstallStateTest.lookupOffersPickOrPaste` exist and pass.
- [ ] `InstallStateTest.planStepDecidesWhenAssigned` and `InstallStateTest.unassignedAppComparesTheBuiltVersion` no longer exist.
- [ ] `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` exits 0.

**Steps:**

- [ ] **Step 1: Write the failing test.** Replace the whole file `android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt` with:

```kotlin
package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.AppBundle
import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.AppStatusDto
import io.github.jhangyu.relume.api.Candidate
import io.github.jhangyu.relume.api.DownloadState
import io.github.jhangyu.relume.api.InstalledPackage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Apps card's one action (spec install-states 1) and its download-URL fix. */
class InstallStateTest {
    private val signer = "cd".repeat(32)

    private fun app(state: String = "up_to_date", version: String? = "1.0", lookup: String = "manual", candidates: Int = 0) = AppObject(
        id = 7, packageName = "p.item.7", name = "App", iconUrl = null,
        bundle = AppBundle(1, "Morphe Patches", "builtin", "morphe", "v1"), appVersion = version,
        status = AppStatusDto(state, null, null, null, null),
        download = DownloadState(
            lookup, null, null, null, null,
            List(candidates) { Candidate("App $it", "https://www.apkmirror.com/apk/o/a$it/", "https://www.apkmirror.com/uploads/?appcategory=a$it") },
            null, null,
        ),
    )

    // The built APK is a renamed package: the comparisons must use it, not the source app's p.item.7.
    private val target = InstallTarget("p.renamed.7", versionCode = 5, versionName = "1.0", patchVersion = "v1.25.2", signerSha256 = signer)

    private fun installed(code: Long, signerSha256: String = signer) = InstalledPackage("p.renamed.7", code, "x", signerSha256)

    @Test
    fun cardActionFollowsTheFourConditionsInOrder() {
        assertEquals(CardAction.Install, cardAction(target, null, null))
        // A different signer wins over an older version: Android cannot update in place.
        assertEquals(CardAction.Reinstall, cardAction(target, installed(4, "ef".repeat(32)), "v1.0.0"))
        assertEquals(CardAction.Installed, cardAction(target, installed(5, signer.uppercase()), "v1.25.2"))   // hex case does not matter
        assertEquals(CardAction.Upgrade, cardAction(target, installed(4), "v1.25.2"))                       // app version older
        assertEquals(CardAction.Upgrade, cardAction(target, installed(5), "v1.25.1"))                       // patch version older
        assertEquals(CardAction.Installed, cardAction(target, installed(6), "v1.26.0"))                     // phone is newer: no downgrade offer
        assertEquals(CardAction.Installed, cardAction(target, installed(5), null))                          // no record: patch not compared
        assertEquals(CardAction.Installed, cardAction(target.copy(signerSha256 = null, versionCode = null, patchVersion = null), installed(1), "v0"))
    }

    @Test
    fun patchVersionComparesLikeTheServer() {
        // Same cases as catalog.version_key (local/webui/catalog.py).
        assertTrue(versionOlder("v1.9.2", "v1.10.0"))       // numeric, not text order
        assertTrue(versionOlder("1.48.0-dev.3", "1.48.0"))  // prerelease before release
        assertTrue(versionOlder("v1.0-rc1", "v1.0"))
        assertFalse(versionOlder("v2.0", "2.0"))            // one leading v is dropped
        assertFalse(versionOlder("1.48.0", "1.48.0-dev.3"))
        assertTrue(versionOlder("1.2", "1.2.1"))            // shorter prefix is older
        assertFalse(versionOlder("20251231000000", "1"))    // date-style tags compare as numbers
    }

    @Test
    fun lookupOffersPickOrPaste() {
        assertEquals(Lookup.PickOne, AppFacts(app("needs_download_url", lookup = "ambiguous", candidates = 2), null, null).lookup)
        assertEquals(Lookup.NeedsUrl, AppFacts(app("needs_download_url", lookup = "not_found"), null, null).lookup)
        assertNull(AppFacts(app(), null, null).lookup)
        assertEquals("2 APKMirror pages match", AppFacts(app("needs_download_url", lookup = "ambiguous", candidates = 2), null, null).statusLine)
    }
}
```

- [ ] **Step 2: Run it to see it fail.**

Run: `cd android && ANDROID_HOME=/opt/homebrew/share/android-commandlinetools ./gradlew testDebugUnitTest --tests io.github.jhangyu.relume.ui.InstallStateTest --console=plain`
Expected: compilation FAIL with `Unresolved reference 'InstallTarget'` / `'cardAction'` / `'versionOlder'`.

- [ ] **Step 3: Models.** In `android/app/src/main/kotlin/io/github/jhangyu/relume/api/Models.kt`:

In `data class Download(...)`, after `val tag: String? = null,` add:

```kotlin
    /** Patch version of an item build (null for project steps and builds without one). */
    @SerialName("patch_version") val patchVersion: String? = null,
```

In `data class AppObject(...)`, after `val category: String? = null,` add:

```kotlin
    /** The built APK this phone would install (server picks its ABI); null while nothing is built. */
    val build: AppBuild? = null,
```

After `AppObject`, add:

```kotlin
/** Facts of the built APK in `GET /api/apps` `build` (spec install-states 1); its package can differ from [AppObject.packageName]. */
@Serializable
data class AppBuild(
    @SerialName("package") val packageName: String,
    @SerialName("version_code") val versionCode: Long?,
    @SerialName("version_name") val versionName: String?,
    @SerialName("patch_version") val patchVersion: String?,
    @SerialName("signer_sha256") val signerSha256: String?,
)
```

In `data class FollowedApp(...)`, after `val files: List<FollowedFile>,` add:

```kotlin
    /** Signer of the downloaded release; null when nothing is downloaded. */
    @SerialName("signer_sha256") val signerSha256: String? = null,
```

Replace `data class FollowedFile(...)` with:

```kotlin
@Serializable
data class FollowedFile(
    val name: String,
    val abi: String?,
    @SerialName("version_name") val versionName: String?,
    val url: String,
    @SerialName("version_code") val versionCode: Long? = null,
)
```

- [ ] **Step 4: The action function.** In `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt` add the import `import io.github.jhangyu.relume.api.FollowedApp` and append at the end of the file:

```kotlin
/** The one action of a card's phone row (spec install-states 1), shared by item and followed-project cards. */
enum class CardAction { Install, Reinstall, Upgrade, Installed }

/** What an install would put on the phone, read from the built APK (its package can differ from the source app's). */
data class InstallTarget(
    val packageName: String,
    val versionCode: Long?,
    val versionName: String?,
    val patchVersion: String?,
    val signerSha256: String?,
)

/**
 * Spec install-states 1, conditions in order. [installed] is looked up by [InstallTarget.packageName]; [installedPatch] is the
 * patch version Relume recorded when it installed the package (null = no record, the patch version is not compared).
 */
internal fun cardAction(target: InstallTarget, installed: InstalledPackage?, installedPatch: String?): CardAction {
    if (installed == null) return CardAction.Install
    val signer = target.signerSha256
    if (signer != null && !installed.signerSha256.equals(signer, ignoreCase = true)) return CardAction.Reinstall
    val code = target.versionCode
    if (code != null && installed.versionCode < code) return CardAction.Upgrade
    val patch = target.patchVersion
    if (installedPatch != null && patch != null && versionOlder(installedPatch, patch)) return CardAction.Upgrade
    return CardAction.Installed
}

private val VERSION_SEPARATORS = Regex("[.\\-+_]")

/** True when [a] sorts before [b] under the server's `catalog.version_key` (local/webui/catalog.py). */
internal fun versionOlder(a: String, b: String): Boolean {
    val x = versionKey(a)
    val y = versionKey(b)
    for (i in 0 until minOf(x.size, y.size)) {
        if (x[i] != y[i]) return x[i] < y[i]
    }
    return x.size < y.size
}

/** One leading v/V dropped; a part of digits is its number, any other part -1; a trailing 0 ranks a release above its prerelease. */
private fun versionKey(v: String): List<Long> {
    val s = if (v.startsWith("v") || v.startsWith("V")) v.substring(1) else v
    // ponytail: a digit run beyond Long saturates to MAX (Python ints are unbounded); no real tag is that long.
    return s.split(VERSION_SEPARATORS).map { p -> if (p.isNotEmpty() && p.all { it in '0'..'9' }) p.toLongOrNull() ?: Long.MAX_VALUE else -1L } + 0L
}

/** The built APK of an item card; null while nothing is built. */
internal val AppObject.target: InstallTarget?
    get() = build?.let { InstallTarget(it.packageName, it.versionCode, it.versionName, it.patchVersion, it.signerSha256) }

/** A followed project's install target: the plan step's file (phone ABI) when planned, else the first listed file. */
internal fun FollowedApp.target(step: PlanStep?): InstallTarget? {
    val d = step?.download
    if (step != null && d != null) return InstallTarget(step.packageName, d.versionCode, d.versionName, d.patchVersion, d.signerSha256)
    val pkg = packageName ?: return null
    if (blocked || files.isEmpty()) return null
    val f = files.first()
    return InstallTarget(pkg, f.versionCode, f.versionName, null, signerSha256)
}
```

The name `InstallTarget` (not `Target`) avoids the default-imported `kotlin.annotation.Target`.

- [ ] **Step 5: Run the test.**

Run: `cd android && ANDROID_HOME=/opt/homebrew/share/android-commandlinetools ./gradlew testDebugUnitTest --tests io.github.jhangyu.relume.ui.InstallStateTest --console=plain`
Expected: `BUILD SUCCESSFUL`; 3 tests in `InstallStateTest` pass.

- [ ] **Step 6: Android leg.**

Run: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android; echo RC=$?`
Expected: `RC=0`.

- [ ] **Step 7: Commit.**

```bash
git rev-parse --abbrev-ref HEAD
git add android/app/src/main/kotlin/io/github/jhangyu/relume/api/Models.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt
git commit -m "feat(android): card action function compares the built APK package, version, patch and signer" -- android/app/src/main/kotlin/io/github/jhangyu/relume/api/Models.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt
git status --porcelain -- android
```

### Task 3: Patch-version record and a terminal state for the uninstall (group B)

**Files:**
- Create: `android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstalledPatches.kt`
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRunner.kt` (`reconcileOnStart`, `onSessionResult`, `onUninstallResult`, new private `recordPatch`)
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRun.kt` (replace `note` with `endUninstall`)

**Interfaces:**
- Consumes: `Download.patchVersion: String?` (Task 2).
- Produces:
  - `object InstalledPatches { fun get(context: Context, packageName: String): String?; internal fun record(context: Context, packageName: String, patchVersion: String) }`
  - `InstallRun.endUninstall(run: RunState, index: Int, outcome: StepOutcome): RunState`
  - Behavior other tasks rely on: after the user cancels the system uninstall prompt the step state is `StepState.CANCELLED`; after any other uninstall failure it is `StepState.FAILED`; after success it is `PENDING` (existing `retry`).

**Behavior:**
- A step that ends `INSTALLED` and whose `download.patchVersion` is not null writes `InstalledPatches.record(ctx, step.packageName, patchVersion)` (step.packageName is the built APK package). Both paths: the session result broadcast (`onSessionResult`) and a lost session found installed at start (`reconcileOnStart` → `lostSession`). The write happens after the run-store update, outside the `update` lambda.
- `onUninstallResult`: `STATUS_FAILURE_ABORTED` → `endUninstall(CANCELLED, USER_REJECTED, "uninstall of <pkg> cancelled by the user")`; other non-success, non-pending statuses → `endUninstall(FAILED, INSTALLER_ERROR, "uninstall of <pkg> failed: <status name>[: message]")`. Pending and success unchanged.
- `endUninstall` requires the step to be `NEEDS_UNINSTALL` (`check` with step index, name and actual state) and keeps `receiptDue` as is.

**Constraints:**
- SharedPreferences file name `relume-patches`, `commit()` checked like `CheckScheduler.record`.
- `InstallRun.note` is deleted (its only caller is replaced).

**Acceptance criteria:**
- [ ] `grep -rn "InstallRun.note\|fun note(" android/app/src/main` prints nothing.
- [ ] `grep -n "InstalledPatches.record" android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRunner.kt` prints exactly one line (inside `recordPatch`).
- [ ] `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` exits 0.

**Steps:** (no committed test: spec limits tests to the action function; the device run in Task 7 D1, D4, D5 covers this)

- [ ] **Step 1: The record.** Create `android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstalledPatches.kt`:

```kotlin
package io.github.jhangyu.relume.install

import android.content.Context

/**
 * Patch version Relume installed, per built APK package (spec install-states 1). Written when an install step ends
 * `installed`; the phone cannot read a patch version from an installed APK.
 */
object InstalledPatches {
    private const val PREFS = "relume-patches"

    /** Null = Relume never recorded an install of [packageName]. */
    fun get(context: Context, packageName: String): String? = prefs(context).getString(packageName, null)

    internal fun record(context: Context, packageName: String, patchVersion: String) {
        val saved = prefs(context).edit().putString(packageName, patchVersion).commit()
        check(saved) { "patch record: SharedPreferences '$PREFS' commit failed for $packageName = $patchVersion" }
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
```

- [ ] **Step 2: Uninstall transition.** In `android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRun.kt` replace

```kotlin
    /** Replaces the error text of step [index] without changing its state or receipt. */
    fun note(run: RunState, index: Int, text: String): RunState = run.replace(index, run.steps[index].copy(errorText = text))
```

with

```kotlin
    /**
     * The system uninstall of `needs_uninstall` step [index] did not succeed: the step ends in [outcome] (`cancelled`
     * when the user declined, else `failed`). Its failure receipt was already due as `needs_uninstall`; none is added.
     */
    fun endUninstall(run: RunState, index: Int, outcome: StepOutcome): RunState {
        val s = run.steps[index]
        check(s.state == StepState.NEEDS_UNINSTALL) { "end uninstall: step $index (${s.step.displayName}) is ${s.state}, expected NEEDS_UNINSTALL" }
        return run.replace(index, s.copy(state = outcome.state, errorCode = outcome.errorCode, errorText = outcome.errorText))
    }
```

- [ ] **Step 3: Runner.** In `android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRunner.kt`:

Add the import `import io.github.jhangyu.relume.api.PlanStep`.

In `reconcileOnStart`, after the `val after = InstallRunStore.update(context) { … }` block and before `if (after != run) …`, add:

```kotlin
        after?.steps?.forEachIndexed { i, s -> if (s.state == StepState.INSTALLED && run.steps[i].state != StepState.INSTALLED) recordPatch(context, s.step) }
```

In `onSessionResult`, replace

```kotlin
        InstallRunStore.update(context) { cur -> cur?.let { InstallRun.finish(it, index, outcome) } }
```

with

```kotlin
        val after = InstallRunStore.update(context) { cur -> cur?.let { InstallRun.finish(it, index, outcome) } }
        if (after?.steps?.get(index)?.state == StepState.INSTALLED) recordPatch(context, run.steps[index].step)
```

In `onUninstallResult`, replace the `else -> { … InstallRun.note … }` branch with:

```kotlin
            PackageInstaller.STATUS_FAILURE_ABORTED -> InstallRunStore.update(context) { cur ->
                cur?.let { InstallRun.endUninstall(it, stepIndex, StepOutcome(StepState.CANCELLED, ErrorCodes.USER_REJECTED, "uninstall of ${s.step.packageName} cancelled by the user")) }
            }
            else -> {
                val text = "uninstall of ${s.step.packageName} failed: ${InstallResult.statusName(status)}" + (message?.let { ": $it" } ?: "")
                InstallRunStore.update(context) { cur -> cur?.let { InstallRun.endUninstall(it, stepIndex, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, text)) } }
            }
```

Add the private helper next to `prompt`:

```kotlin
    /** Records the patch version of a step that ended `installed` under its built APK package (spec install-states 1). */
    private fun recordPatch(context: Context, step: PlanStep) {
        step.download?.patchVersion?.let { InstalledPatches.record(context, step.packageName, it) }
    }
```

`when (status)` in `onUninstallResult` becomes four branches: `STATUS_PENDING_USER_ACTION`, `STATUS_SUCCESS`, `STATUS_FAILURE_ABORTED`, `else`. The `when` is a statement, so the `update` return value is discarded.

- [ ] **Step 4: Checks.**

Run: `grep -rn "InstallRun.note\|fun note(" android/app/src/main; grep -n "InstalledPatches.record" android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRunner.kt`
Expected: no line from the first grep; one line from the second.

Run: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android; echo RC=$?`
Expected: `RC=0` (`InstallRunTest`, `InstallPersistenceTest` unchanged and green).

- [ ] **Step 5: Commit.**

```bash
git rev-parse --abbrev-ref HEAD
git add android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstalledPatches.kt android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRunner.kt android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRun.kt
git commit -m "feat(android): record the installed patch version and end a declined uninstall as cancelled" -- android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstalledPatches.kt android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRunner.kt android/app/src/main/kotlin/io/github/jhangyu/relume/install/InstallRun.kt
git status --porcelain -- android
```

### Task 4: Apps cards use the one action function; reinstall confirm sheet (group C)

**Files:**
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt` (reshape `AppFacts`; delete `PhoneState`, `phoneState`, `installable`, `phone`, `target: String?`)
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt` (`facts`, `followedTarget`, `followedAction`, delete `followedPhone`; install-sheet state)
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppsScreen.kt` (filters, `AppCard`, one shared `PhoneRow`, `PhonePill`, `FollowedCard`, reinstall ask)
- Create: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/ReinstallSheet.kt` (`RSheet`, `ReinstallSheet`)
- Modify: `android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt` (the `AppFacts` constructor in `lookupOffersPickOrPaste`)

**Interfaces:**
- Consumes: Task 2 (`CardAction`, `InstallTarget`, `cardAction`, `AppObject.target`, `FollowedApp.target`), Task 3 (`InstalledPatches.get`).
- Produces (Task 5 relies on these exact names):
  - `class AppFacts(val app: AppObject, val target: InstallTarget?, val installed: InstalledPackage?, val installedPatch: String?)` with `val building: Boolean`, `val action: CardAction?`, `val lookup: Lookup?`, `val statusLine: String?`
  - `AppModel.sheetOpen: Boolean` (Compose state, private set) — true after a run start (`StartResult.Started` or `Corrupt`)
  - `AppModel.closeSheet()` — sets `sheetOpen = false` and `reinstallPackage = null`
  - `AppModel.retryInstall()` — re-runs `beginInstall` with the last `(kind, id)`
  - `AppModel.reinstallPackage: String?` (Compose state, private set) — built APK package whose `needs_uninstall` step the user already confirmed
  - `AppModel.markReinstall(packageName: String)`; `AppModel.beginReinstall(kind: StepKind, id: Int, packageName: String)`
  - `AppModel.followedTarget(app: FollowedApp): InstallTarget?`; `AppModel.followedAction(app: FollowedApp): CardAction?`
  - `@Composable internal fun RSheet(onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit)`
  - `@Composable fun ReinstallSheet(name: String, detail: String, onDismiss: () -> Unit, onConfirm: () -> Unit)` (`detail` = subtitle, e.g. "9.0.8 · signed by another key")
  - `AppModel.runStarted` stays in this task (its reader is `RelumeRoot`, owned by Task 5); Task 5 deletes it.

**Behavior:**
- Item card: `facts(app)` looks up `installed` and the patch record by `app.target.packageName` (the built APK), never `app.packageName`.
- Phone row (shared by item and followed cards): pill + one button. `installing` → "Installing <version>" pill, no button. `action == null` (no build / blocked / no files) → nothing. `Installed` → "Installed <installed versionName>" pill. `Upgrade` → "Phone has <installed versionName>" pill + **Upgrade** button (or "Upgrade after build" text while building). `Reinstall` → "Different signer" pill + **Uninstall & reinstall** button (hidden while building). `Install` → no pill + **Install <versionName>** button (hidden while building).
- Filters: "Not installed" = action `Install`; "Phone update" = action `Upgrade` or `Reinstall`; same rule for followed cards.
- Uninstall & reinstall opens `ReinstallSheet` (mockup frame 4): title "Reinstall <name>?", subtitle "<version> · signed by another key", danger box saying data and login are erased, two hint lines, Cancel and **Erase and reinstall**. Confirm → `beginReinstall(kind, id, target.packageName)`; Cancel / swipe → nothing changes.
- Card subtitle shows `<appVersion> · patch <patchVersion>` when the build has a patch version, else `<appVersion> · <bundle name>` (mockup frame 1).
- The ERROR/UP_TO_DATE plan step no longer hides Install: the action depends only on the target and the phone. Pressing Install on a server-error plan shows that error in the lightbox (Task 5).

**Constraints:**
- `RSheet` is the bottom sheet frame for this feature (Material3 `ModalBottomSheet`, `skipPartiallyExpanded = true`, `surfaceContainerLow`), same look as the private `SheetFrame` in `SchedulesScreen.kt` (not touched).
- No new dependencies.

**Acceptance criteria:**
- [ ] `grep -rn "PhoneState\|phoneState\|installable\|followedPhone" android/app/src` prints nothing.
- [ ] `grep -n "installed\[app.packageName\]" android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt` prints nothing.
- [ ] `grep -c "cardAction(" android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt` prints `AppModel.kt:1` (followed cards) and `AppStatus.kt:2` (the definition and the item call in `AppFacts.action`).
- [ ] `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` exits 0.

**Steps:**

- [ ] **Step 1: Test constructor first (it stops compiling once `AppFacts` changes).** In `InstallStateTest.lookupOffersPickOrPaste` replace every `AppFacts(<app>, null, null)` with `AppFacts(<app>, null, null, null)` (4 places). Run `cd android && ANDROID_HOME=/opt/homebrew/share/android-commandlinetools ./gradlew testDebugUnitTest --tests io.github.jhangyu.relume.ui.InstallStateTest --console=plain`. Expected: compilation FAIL `No value passed`/`Too many arguments for constructor AppFacts` (proves the test reads the new constructor).

- [ ] **Step 2: `AppFacts` on the built APK.** In `ui/AppStatus.kt`:
  - delete `enum class PhoneState { … }` with its KDoc, `internal fun phoneState(…)` with its KDoc, and the import `io.github.jhangyu.relume.api.PlanAction`;
  - replace the class header and the members `target`, `phone` and `installable` so the class reads (keep `lookup` and `statusLine` bodies exactly as they are):

```kotlin
/** What the phone knows about one enabled app: the built APK it would install, that package on the phone, its recorded patch version. */
class AppFacts(val app: AppObject, val target: InstallTarget?, val installed: InstalledPackage?, val installedPatch: String?) {
    val building: Boolean get() = app.status.state == STATE_BUILDING || app.status.state == STATE_QUEUED

    /** The card's one action (spec install-states 1); null while nothing is built. */
    val action: CardAction? get() = target?.let { cardAction(it, installed, installedPatch) }

    // lookup and statusLine: unchanged
}
```

- [ ] **Step 3: `AppModel`.** In `ui/AppModel.kt`:
  - add import `io.github.jhangyu.relume.install.InstalledPatches`;
  - replace `fun facts(…)`, `fun followedTarget(…)` and `fun followedPhone(…)` (lines 98-110) with:

```kotlin
    /** Card facts looked up by the built APK package, never the source app's package (a patch can rename it). */
    fun facts(app: AppObject): AppFacts {
        val t = app.target
        return AppFacts(app, t, t?.let { installed[it.packageName] }, t?.let { InstalledPatches.get(relume, it.packageName) })
    }

    fun followedStep(app: FollowedApp): PlanStep? = plan?.steps?.firstOrNull { it.refId == app.id && it.kind == StepKind.PROJECT }

    /** What Install would put on the phone: the plan step's file, else the first listed file. */
    fun followedTarget(app: FollowedApp): InstallTarget? = app.target(followedStep(app))

    /** The followed card's action (same function as item cards); null when the project has nothing to install. */
    fun followedAction(app: FollowedApp): CardAction? =
        followedTarget(app)?.let { cardAction(it, installed[it.packageName], InstalledPatches.get(relume, it.packageName)) }
```

(the existing `followedStep` line stays once — do not duplicate it).
  - after `var runStarted by mutableStateOf(0) / private set` add:

```kotlin
    /** The install lightbox is open (spec install-states 2): set when a run starts, cleared by [closeSheet]. */
    var sheetOpen by mutableStateOf(false)
        private set

    /** Built APK package whose `needs_uninstall` step the user already confirmed; the lightbox then opens the system uninstall. */
    var reinstallPackage by mutableStateOf<String?>(null)
        private set

    /** Card of the last install start, for the lightbox Retry. */
    private var lastInstall: Pair<StepKind, Int>? = null

    fun closeSheet() {
        sheetOpen = false
        reinstallPackage = null
    }

    fun markReinstall(packageName: String) {
        reinstallPackage = packageName
    }

    /** "Uninstall & reinstall" confirmed (spec install-states 3): the run's `needs_uninstall` step goes straight to the system prompt. */
    fun beginReinstall(kind: StepKind, id: Int, packageName: String) {
        markReinstall(packageName)
        beginInstall(kind, id)
    }

    fun retryInstall() {
        val (kind, id) = checkNotNull(lastInstall) { "install retry: expected an install started from a card in this session, found none" }
        beginInstall(kind, id)
    }
```

  - first line inside `fun beginInstall(kind: StepKind, id: Int) {`: `lastInstall = kind to id`;
  - in `private fun start(p: InstallPlan)` replace the `Started, Corrupt` branch and its comment with:

```kotlin
            // Corrupt: the install sheet shows the unreadable run file and its reset.
            StartResult.Started, StartResult.Corrupt -> {
                runStarted++
                sheetOpen = true
            }
```

  - in `signOutLocally`, after `health = null` add `closeSheet()`.

- [ ] **Step 4: Reinstall sheet.** Create `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/ReinstallSheet.kt`:

```kotlin
package io.github.jhangyu.relume.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Bottom sheet frame of the install lightbox and the reinstall confirm (mockup install-lightbox-b). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun RSheet(onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
    ) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            content = content,
        )
    }
}

/** Mockup frame 4: data-loss confirm before the system uninstall of an app signed by another key (spec install-states 3). */
@Composable
fun ReinstallSheet(name: String, detail: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    RSheet(onDismiss) {
        Txt("Reinstall $name?", style = MaterialTheme.typography.headlineSmall)
        Txt(detail, style = MaterialTheme.typography.bodySmall, color = muted())
        Banner(
            "Data and login are erased",
            Tone.Danger,
            detail = "Android cannot update the copy on your phone in place, so Relume must remove it first. You will sign in again.",
        )
        Txt("Android shows its uninstall prompt next. After that, the new build installs automatically.", style = MaterialTheme.typography.bodyMedium, color = muted())
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Erase and reinstall", onConfirm, Modifier.weight(1f), kind = ButtonKind.Danger)
        }
    }
}
```

- [ ] **Step 5: Apps cards.** In `ui/AppsScreen.kt` replace lines 41-232 (from `private enum class AppFilter` through the end of `FollowedCard`) with the code below; `UrlDialog` and `PickDialog` stay unchanged. Keep the existing imports (all still used).

```kotlin
private enum class AppFilter(val title: String, val matches: (AppFacts) -> Boolean, val followed: (CardAction?) -> Boolean) {
    All("All", { true }, { true }),
    NotInstalled("Not installed", { it.action == CardAction.Install }, { it == CardAction.Install }),
    PhoneUpdate("Phone update", { it.action.updates }, { it.updates }),
    Failed("Failed", { it.app.status.state == STATE_FAILED }, { false }),
    NeedsUrl("Needs URL", { it.lookup != null }, { false }),
}

/** The phone has an older or differently signed copy. */
private val CardAction?.updates: Boolean get() = this == CardAction.Upgrade || this == CardAction.Reinstall

/** A pending "Uninstall & reinstall" confirm. */
private class ReinstallAsk(val kind: StepKind, val id: Int, val name: String, val target: InstallTarget)

/** Apps tab: filter chips, one card per enabled app, pull-to-refresh. Builds reach the phone only through the card action. */
@Composable
fun AppsScreen(model: AppModel, onOpenSettings: (Int) -> Unit, onOpenLog: (Int, Int?) -> Unit) {
    var filter by rememberSaveable { mutableIntStateOf(0) }
    var fixing by remember { mutableStateOf<Pair<AppObject, Lookup>?>(null) }
    var reinstall by remember { mutableStateOf<ReinstallAsk?>(null) }
    val all = model.apps.map(model::facts)
    val visible = all.filter { AppFilter.entries[filter].matches(it) }
    val followed = model.followed.map { it to model.followedAction(it) }
    val visibleFollowed = followed.filter { (_, a) -> AppFilter.entries[filter].followed(a) }
    val run by InstallRunner.state.collectAsState()
    val installing = run?.steps.orEmpty()
        .filter { it.step.kind == StepKind.ITEM && it.state.active }.map { it.step.refId }.toSet()
    val installingProjects = run?.steps.orEmpty()
        .filter { it.step.kind == StepKind.PROJECT && it.state.active }.map { it.step.refId }.toSet()

    Column(Modifier.fillMaxSize()) {
        AppBar("Apps", actions = {
            IconAction(R.drawable.ms_sync, "Check all apps for updates", model::runSchedule, IconTone.Sync)
            Avatar()
        })
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        ChipRow(AppFilter.entries.map { f -> if (f == AppFilter.All) "All ${all.size + followed.size}" else "${f.title} ${all.count(f.matches) + followed.count { f.followed(it.second) }}" }, filter) { filter = it }
        PullToRefreshBox(isRefreshing = model.refreshing, onRefresh = model::refresh, modifier = Modifier.weight(1f)) {
            LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = ScreenPadding, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(visible, key = { it.app.id }) { f ->
                    AppCard(f, model, f.app.id in installing, onOpenSettings, onOpenLog, onFix = { fixing = f.app to it }, onReinstall = { reinstall = it })
                }
                items(visibleFollowed, key = { "followed-${it.first.id}" }) { (app, action) ->
                    FollowedCard(app, action, app.id in installingProjects, model) { reinstall = it }
                }
                if (visible.isEmpty() && visibleFollowed.isEmpty() && !model.refreshing) {
                    val empty = when {
                        !model.loaded -> model.loadError?.let { "Apps could not be loaded: $it. Pull down to try again." } ?: "Loading…"
                        all.isEmpty() && followed.isEmpty() -> "No apps enabled. Enable one from the Marketplace."
                        else -> "Nothing in this filter."
                    }
                    item { Txt(empty, color = muted()) }
                }
            }
        }
    }
    fixing?.let { (app, kind) ->
        val close = { fixing = null }
        when (kind) {
            Lookup.NeedsUrl -> UrlDialog(app, close) { model.setListUrl(app.id, it, close) }
            Lookup.PickOne -> PickDialog(app, close) { model.setListUrl(app.id, it, close) }
        }
    }
    reinstall?.let { r ->
        ReinstallSheet(r.name, listOfNotNull(r.target.versionName, "signed by another key").joinToString(" · "), onDismiss = { reinstall = null }) {
            reinstall = null
            model.beginReinstall(r.kind, r.id, r.target.packageName)
        }
    }
}

@Composable
private fun AppCard(
    f: AppFacts,
    model: AppModel,
    installing: Boolean,
    onOpenSettings: (Int) -> Unit,
    onOpenLog: (Int, Int?) -> Unit,
    onFix: (Lookup) -> Unit,
    onReinstall: (ReinstallAsk) -> Unit,
) {
    val app = f.app
    val status = app.status
    Surface(
        Modifier.fillMaxWidth().clickable { onOpenSettings(app.id) },
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
    ) {
        // Mockup .acard: 16 dp padding, no bottom padding (the install row closes the card); trailing icon buttons pulled out 12 dp.
        Column(Modifier.padding(top = 16.dp)) {
            Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                AppTile(app.name, app.iconUrl, model.api, category = app.category)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Txt(app.name, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                    val second = app.build?.patchVersion?.let { "patch $it" } ?: app.bundle.name
                    Txt(listOfNotNull(app.appVersion, second).joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = muted(), maxLines = 1)
                }
                StatusPill(status.state)
            }
            status.progress?.let { p ->
                LinearProgressIndicator(
                    { p.index.toFloat() / p.count },
                    Modifier.fillMaxWidth().padding(start = 16.dp, top = 12.dp, end = 16.dp),
                    color = MaterialTheme.relume.blue.onContainer,
                    trackColor = MaterialTheme.relume.blue.container,
                )
            }
            Row(
                Modifier.fillMaxWidth().padding(start = 16.dp, top = 4.dp, end = 4.dp).heightIn(min = TouchTarget),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                val line = f.statusLine
                Txt(
                    line.orEmpty(),
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall,
                    color = if (status.state == STATE_FAILED) MaterialTheme.colorScheme.error else muted(),
                    maxLines = 2,
                )
                val job = status.jobId
                if (job != null && (f.building || status.state == STATE_FAILED)) {
                    ActionButton(if (f.building) "Live log" else "Log", { onOpenLog(job, app.id) }, kind = ButtonKind.Text)
                }
                when (f.lookup) {
                    Lookup.NeedsUrl -> ActionButton("Add URL", { onFix(Lookup.NeedsUrl) }, kind = ButtonKind.Text)
                    Lookup.PickOne -> ActionButton("Pick one", { onFix(Lookup.PickOne) }, kind = ButtonKind.Text)
                    null -> Unit
                }
                IconAction(R.drawable.ms_sync, "Check ${app.name} for updates", { model.check(app.id) }, IconTone.Sync, enabled = !f.building)
                val tonal = status.state == "update_available" || status.state == STATE_FAILED
                IconAction(
                    R.drawable.ms_build,
                    if (status.state == STATE_FAILED) "Retry ${app.name} build" else "Rebuild ${app.name}",
                    { model.rebuild(app.id) { job -> onOpenLog(job, app.id) } },
                    if (tonal) IconTone.Build else IconTone.Plain,
                    enabled = !f.building && status.state != STATE_NEEDS_URL,
                )
            }
            Hairline()
            PhoneRow(
                f.action, f.target, f.installed, installing, f.building,
                onInstall = { model.beginInstall(StepKind.ITEM, app.id) },
                onReinstall = { onReinstall(ReinstallAsk(StepKind.ITEM, app.id, app.name, checkNotNull(f.target) { "reinstall of ${app.name}: expected a built APK, found none" })) },
            )
        }
    }
}

/** Bottom row of a card, this phone (spec install-states 1): state pill on the left, the one action on the right. */
@Composable
private fun PhoneRow(
    action: CardAction?,
    target: InstallTarget?,
    installed: InstalledPackage?,
    installing: Boolean,
    building: Boolean,
    onInstall: () -> Unit,
    onReinstall: () -> Unit,
) {
    // Mockup .inst: full-bleed divider above, min 56 dp, padding 8 16 dp, content centred, 8 dp gaps.
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PhonePill(installing, action, target, installed)
        Spacer(Modifier.weight(1f))
        when {
            installing || action == null || action == CardAction.Installed -> Unit
            building -> if (action == CardAction.Upgrade) Txt("Upgrade after build", style = MaterialTheme.typography.bodySmall, color = muted())
            action == CardAction.Install ->
                ActionButton(listOfNotNull("Install", target?.versionName).joinToString(" "), onInstall, kind = ButtonKind.Install, icon = R.drawable.ms_install_mobile, size = ButtonSize.Small)
            action == CardAction.Upgrade ->
                ActionButton("Upgrade", onInstall, kind = ButtonKind.Install, icon = R.drawable.ms_system_update, size = ButtonSize.Small)
            action == CardAction.Reinstall ->
                ActionButton("Uninstall & reinstall", onReinstall, kind = ButtonKind.Danger, icon = R.drawable.ms_warning, size = ButtonSize.Small)
        }
    }
}

/** The state pill of a phone row (mockup frame 1): installing, installed, older copy, other signer; none when not installed. */
@Composable
private fun PhonePill(installing: Boolean, action: CardAction?, target: InstallTarget?, installed: InstalledPackage?) {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    when {
        installing -> Pill("Installing ${target?.versionName.orEmpty()}".trim(), r.blue.container, r.blue.onContainer, R.drawable.ms_progress_activity)
        action == CardAction.Installed -> Pill("Installed ${installed?.versionName.orEmpty()}".trim(), r.green.container, r.green.onContainer, R.drawable.ms_check)
        action == CardAction.Upgrade -> Pill("Phone has ${installed?.versionName ?: "an older version"}", r.lavender.container, r.lavender.onContainer, R.drawable.ms_system_update)
        action == CardAction.Reinstall -> Pill("Different signer", s.errorContainer, s.onErrorContainer, R.drawable.ms_error)
    }
}

/** Followed-project app: the head and the same phone row as item cards. */
@Composable
private fun FollowedCard(app: FollowedApp, action: CardAction?, installing: Boolean, model: AppModel, onReinstall: (ReinstallAsk) -> Unit) {
    val target = model.followedTarget(app)
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
            PhoneRow(
                action, target, target?.let { model.installed[it.packageName] }, installing, building = false,
                onInstall = { model.beginInstall(StepKind.PROJECT, app.id) },
                onReinstall = { onReinstall(ReinstallAsk(StepKind.PROJECT, app.id, app.name, checkNotNull(target) { "reinstall of ${app.name}: expected a release file, found none" })) },
            )
        }
    }
}
```

- [ ] **Step 6: Run the test and the greps.**

Run: `cd android && ANDROID_HOME=/opt/homebrew/share/android-commandlinetools ./gradlew testDebugUnitTest --tests io.github.jhangyu.relume.ui.InstallStateTest --console=plain`
Expected: `BUILD SUCCESSFUL`, 3 tests pass.

Run: `grep -rn "PhoneState\|phoneState\|installable\|followedPhone" android/app/src; grep -n "installed\[app.packageName\]" android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt`
Expected: no output.

Run: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android; echo RC=$?`
Expected: `RC=0`.

- [ ] **Step 7: Commit.**

```bash
git rev-parse --abbrev-ref HEAD
git add android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppsScreen.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/ReinstallSheet.kt android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt
git commit -m "feat(android): item and followed cards share one action on the built APK, reinstall confirm sheet" -- android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppStatus.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppsScreen.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/ReinstallSheet.kt android/app/src/test/kotlin/io/github/jhangyu/relume/ui/InstallStateTest.kt
git status --porcelain -- android
```

### Task 5: Install lightbox replaces `Screen.Install` (group D)

**Files:**
- Rename + modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallScreen.kt` → `.../ui/InstallSheet.kt` (`git mv`)
- Modify: `.../ui/RelumeRoot.kt:94-96` (delete the `runStarted` effect), `:136` (delete the `Screen.Install` branch), `:145` (show the sheet)
- Modify: `.../ui/Screen.kt:15` (delete `Install`)
- Modify: `.../ui/AppModel.kt` (delete `runStarted` and its increment; nothing else)

**Interfaces:**
- Consumes: Task 4 (`AppModel.sheetOpen`, `closeSheet()`, `retryInstall()`, `reinstallPackage`, `markReinstall(pkg)`, `RSheet`, `ReinstallSheet`), Task 3 (`CANCELLED` / `FAILED` after the uninstall prompt), existing `InstallRunner.state`, `InstallRunner.corrupt`, `InstallRunner.cancel`, `InstallRunner.reset`, `InstallRunner.uninstallAndRetry(ctx, index)`, `InstallRunner.confirm(ctx, index)`.
- Produces:
  - `@Composable fun InstallSheet(run: RunState?, model: AppModel)`
  - `internal enum class SheetOutcome { Running, Done, Failed }`
  - `internal fun sheetOutcome(run: RunState, reinstallPending: String?): SheetOutcome`
  - `InstallPrompts(model)` keeps its name and signature (now in `InstallSheet.kt`).

**Behavior:**
- `RelumeRoot` shows `InstallSheet(run, model)` over any screen while `model.sheetOpen`; `AppModel.start` sets it on `Started` and `Corrupt` (Task 4).
- `sheetOutcome`: `Running` if any step is active or `PENDING`, or a `NEEDS_UNINSTALL` step's package equals `reinstallPending`; `Done` if every step is `INSTALLED`, `UP_TO_DATE` or `CANCELLED`; else `Failed`.
- `Done` (and no corrupt run file) → `model.closeSheet()` at once; the cards refresh through the existing `run.synced` → `model.refresh()` effect in `RelumeRoot`.
- `Failed` → sheet stays: title "Install failed", the step blocks with their error banners, buttons **Close** (`closeSheet`) and **Retry** (`retryInstall`).
- `Running` → title "Installing <name>" (single step) or "Installing <n> apps", the step blocks (tile, version, progress bar while downloading, phase rail, Confirm button for `AWAITING_USER`), a **Cancel** text button (`InstallRunner.cancel`) and the hint "Closes by itself when done".
- Reinstall chain: when a step is `NEEDS_UNINSTALL` and its package equals `model.reinstallPackage`, the sheet calls `InstallRunner.uninstallAndRetry(ctx, index)` once per (run revision, step index). System uninstall success → step re-runs → `INSTALLED` → `Done` → sheet closes. User cancels → `CANCELLED` → `Done` → sheet closes, card unchanged ("Uninstall & reinstall" stays because the signer still differs).
- A `NEEDS_UNINSTALL` step without a pending confirm (e.g. Upgrade hit a conflict) shows "Uninstall and install…"; it opens `ReinstallSheet`; confirm → `model.markReinstall(step.packageName)`, which starts the same chain.
- Corrupt run file → sheet shows the existing corrupt banner and "Reset install run"; it does not auto-close.
- Swipe-down / scrim tap → `closeSheet()` (the run continues in the background, as leaving the old screen did).

**Constraints:**
- `InstallScreen`, `UninstallDialog` and `Screen.Install` are deleted (no shim). `StepBlock`, `PhaseList`, `phaseMarks`, `label`, `pastel`, `conflictTitle`, `InstallPrompts`, `DependencyRow` move with the file.
- No UI tests.

**Acceptance criteria:**
- [ ] `test -f android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallSheet.kt && ! test -e android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallScreen.kt` exits 0.
- [ ] `grep -rn "Screen.Install\|InstallScreen\|UninstallDialog\|runStarted" android/app/src` prints nothing.
- [ ] `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` exits 0.

**Steps:** (no committed test: UI; Task 7 D1, D3, D4, D5 run it on the device)

- [ ] **Step 1: Rename.** `git mv android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallScreen.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallSheet.kt`

- [ ] **Step 2: Sheet code.** In `InstallSheet.kt`:
  - imports: remove `androidx.compose.foundation.layout.fillMaxSize`, `androidx.compose.foundation.rememberScrollState`, `androidx.compose.foundation.verticalScroll`; add `androidx.compose.runtime.LaunchedEffect`. All other imports stay.
  - delete `fun InstallScreen(…)` (with its KDoc "Frames 5a and 5b…") and `private fun UninstallDialog(…)` (with its KDoc "Frame 5c…").
  - keep unchanged: the `PHASE_*`/`PHASES`/`KEY_PREVIEW` constants, `PhaseMark`, `phaseMarks`, `StepState.label()`, `StepState.pastel()`, `StepBlock`, `conflictTitle`, `PhaseList`, `InstallPrompts`, `DependencyRow`.
  - insert, where `InstallScreen` was:

```kotlin
/** Lightbox state (spec install-states 2): open while running, closes itself on success, stays open on failure. */
internal enum class SheetOutcome { Running, Done, Failed }

private val CLOSING = setOf(StepState.INSTALLED, StepState.UP_TO_DATE, StepState.CANCELLED)

/** [reinstallPending]: package whose `needs_uninstall` step the user confirmed; the system uninstall comes next. */
internal fun sheetOutcome(run: RunState, reinstallPending: String?): SheetOutcome = when {
    run.steps.any { it.state.active || it.state == StepState.PENDING } -> SheetOutcome.Running
    run.steps.any { it.state == StepState.NEEDS_UNINSTALL && it.step.packageName == reinstallPending } -> SheetOutcome.Running
    run.steps.all { it.state in CLOSING } -> SheetOutcome.Done
    else -> SheetOutcome.Failed
}

private fun sheetTitle(run: RunState, outcome: SheetOutcome): String {
    if (outcome == SheetOutcome.Failed) return "Install failed"
    val installs = run.steps.filter { it.step.action == PlanAction.INSTALL }
    return if (installs.size == 1) "Installing ${installs[0].step.displayName}" else "Installing ${installs.size} apps"
}

/** Mockup install-lightbox-b frames 2 and 3: the install run as a bottom sheet over the current tab. */
@Composable
fun InstallSheet(run: RunState?, model: AppModel) {
    val ctx = LocalContext.current
    val corrupt by InstallRunner.corrupt.collectAsState()
    var confirmIndex by remember { mutableStateOf<Int?>(null) }
    // "<revision>/<step>" whose system uninstall was already opened: one prompt per step, not one per recomposition.
    var asked by remember { mutableStateOf<String?>(null) }
    val pending = model.reinstallPackage
    val outcome = run?.let { sheetOutcome(it, pending) }

    // Spec install-states 3: a confirmed needs_uninstall step goes to the system uninstall prompt; success re-runs it.
    LaunchedEffect(run, pending) {
        val r = run ?: return@LaunchedEffect
        val i = r.steps.indexOfFirst { it.state == StepState.NEEDS_UNINSTALL && it.step.packageName == pending }
        val key = "${r.revision}/$i"
        if (i >= 0 && asked != key) {
            asked = key
            InstallRunner.uninstallAndRetry(ctx, i)
        }
    }
    LaunchedEffect(outcome, corrupt) {
        if (outcome == SheetOutcome.Done && corrupt == null) model.closeSheet()
    }

    RSheet(model::closeSheet) {
        corrupt?.let { e ->
            Banner("The saved install run is corrupt", Tone.Danger, detail = e.message)
            ActionButton("Reset install run", { InstallRunner.reset(ctx) }, Modifier.fillMaxWidth(), kind = ButtonKind.Danger)
            return@RSheet
        }
        if (run == null || outcome == null) {
            Txt("No install run.", color = muted())
            return@RSheet
        }
        Txt(sheetTitle(run, outcome), style = MaterialTheme.typography.headlineSmall)
        if (run.stalePlan) Banner("The plan changed on the server", Tone.Warn, detail = "Nothing was reinstalled. Retry fetches the new plan.")
        run.syncError?.let { Banner("Could not report to the server", Tone.Danger, detail = it) }
        run.outboxError?.let { Banner("Could not send an earlier result to the server", Tone.Danger, detail = it) }
        run.steps.forEachIndexed { i, s -> StepBlock(s, i, ctx, model) { confirmIndex = i } }
        DialogButtons {
            if (outcome == SheetOutcome.Failed) {
                ActionButton("Close", model::closeSheet, Modifier.weight(1f), kind = ButtonKind.Text)
                ActionButton("Retry", model::retryInstall, Modifier.weight(1f), kind = ButtonKind.Install)
            } else {
                ActionButton("Cancel", { InstallRunner.cancel(ctx) }, Modifier.weight(1f), kind = ButtonKind.Text)
            }
        }
        if (outcome != SheetOutcome.Failed) Txt("Closes by itself when done", style = MaterialTheme.typography.bodySmall, color = muted())
    }

    val index = confirmIndex
    val waiting = if (index != null) run?.steps?.getOrNull(index) else null
    if (index != null && waiting != null && waiting.state == StepState.NEEDS_UNINSTALL) {
        val detail = listOfNotNull(waiting.step.download?.versionName, conflictTitle(waiting.step)).joinToString(" · ")
        ReinstallSheet(waiting.step.displayName, detail, onDismiss = { confirmIndex = null }) {
            confirmIndex = null
            model.markReinstall(waiting.step.packageName)   // the LaunchedEffect above opens the system uninstall
        }
    }
}
```

`StepBlock` keeps its `NEEDS_UNINSTALL` branch (`Banner(conflictTitle(step) …)` + button "Uninstall and install…" calling `onUninstall`), which now opens `ReinstallSheet` via `confirmIndex`.

- [ ] **Step 3: Root and navigation.**
  - `ui/Screen.kt`: delete the line `@Serializable data object Install : Screen`.
  - `ui/RelumeRoot.kt`: delete

```kotlin
    LaunchedEffect(model.runStarted) {
        if (model.runStarted > 0 && stack.last() != Screen.Install) stack = stack + Screen.Install
    }
```

    delete the branch `Screen.Install -> InstallScreen(run, model, onClose = back)`, and replace the last line of `RootContent`, `InstallPrompts(model)`, with:

```kotlin
    InstallPrompts(model)
    if (model.sheetOpen) InstallSheet(run, model)
```

  - `ui/AppModel.kt`: delete

```kotlin
    /** Bumped when a run starts; the root opens the install screen. */
    var runStarted by mutableStateOf(0)
        private set
```

    and the line `runStarted++` in `start` (the branch keeps `sheetOpen = true`).

- [ ] **Step 4: Build and greps.**

Run: `grep -rn "Screen.Install\|InstallScreen\|UninstallDialog\|runStarted" android/app/src; test -e android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallScreen.kt; echo EXISTS_RC=$?`
Expected: no grep output; `EXISTS_RC=1`.

Run: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android; echo RC=$?`
Expected: `RC=0` (compiles `main`; Kotlin warns nothing new about unused imports — remove any it reports).

- [ ] **Step 5: Commit.**

```bash
git rev-parse --abbrev-ref HEAD
git add android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallSheet.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/RelumeRoot.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/Screen.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt
git commit -m "feat(android): install lightbox bottom sheet replaces the install screen, reinstall chain" -- android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallScreen.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/InstallSheet.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/RelumeRoot.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/Screen.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/AppModel.kt
git status --porcelain -- android
```

### Task 6: "Daily at <time>" background check (group A)

**Files:**
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/api/Prefs.kt` (`CheckInterval.DAILY`, `dailyCheckMinute`)
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckScheduler.kt` (`apply`, new `delayUntil`)
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckWorker.kt:40` (corrupt-run message names the install sheet)
- Modify: `android/app/src/main/kotlin/io/github/jhangyu/relume/ui/SettingsScreen.kt:37` and the Background section (`:78-87`)

**Interfaces:**
- Consumes: `RelumeApp.prefs: Prefs`; androidx.work 2.12.0 (verified with `javap` on the cached jar: `PeriodicWorkRequest.Builder(Class, java.time.Duration)`, `WorkRequest.Builder.setInitialDelay(java.time.Duration)`, `Constraints.Builder.setRequiresCharging(boolean)`, `setRequiredNetworkType(NetworkType)`, `ExistingPeriodicWorkPolicy.CANCEL_AND_REENQUEUE`); material3 1.4.0 (verified with `javap`: `rememberTimePickerState(Int, Int, Boolean)`, `TimeInput(TimePickerState, …)`, `TimePickerState.hour/minute`, both `@ExperimentalMaterial3Api`).
- Produces:
  - `enum class CheckInterval { OFF, H1, H6, H24, DAILY }`
  - `Prefs.dailyCheckMinute: Int` (minutes after local midnight, default `3 * 60 + 15`)
  - `CheckScheduler.apply(context: Context, interval: CheckInterval)` — signature unchanged (callers `RelumeRoot.kt:105`, `AppModel.signOutLocally`, `SettingsScreen` need no change)
  - `internal fun CheckScheduler.delayUntil(now: ZonedDateTime, minuteOfDay: Int): Duration`

**Behavior:**
- `DAILY`: periodic work every 24 h, initial delay `delayUntil(ZonedDateTime.now(), prefs.dailyCheckMinute)` (today's time if still ahead, else tomorrow's), constraints `NetworkType.UNMETERED` + `setRequiresCharging(true)`. H1/H6/H24 keep `NetworkType.CONNECTED` and no initial delay.
- All intervals enqueue with `CANCEL_AND_REENQUEUE`, so a new time or interval replaces the pending work.
- Settings chips: "Off", "1 h", "6 h", "24 h", "Daily at HH:MM". With Daily selected, a row "Check time" (supporting "HH:MM · while charging on unmetered Wi-Fi") opens a dialog with `TimeInput` (24 h); Save stores the minute and re-applies the schedule.
- `dailyCheckMinute` setter rejects values outside 0..1439 with `require` naming the value.
- Background install runs and their silent update are unchanged (`InstallWorker.constraints(BACKGROUND)`, `Installer.create`).

**Constraints:**
- No `AlarmManager`, no `SCHEDULE_EXACT_ALARM`.
- The clock enters `delayUntil` as a parameter (testable), but no test is committed (spec: device run).

**Acceptance criteria:**
- [ ] `grep -n "DAILY" android/app/src/main/kotlin/io/github/jhangyu/relume/api/Prefs.kt android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckScheduler.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/SettingsScreen.kt` prints a line in each file.
- [ ] `grep -rn "SCHEDULE_EXACT_ALARM\|AlarmManager" android/app/src/main` prints nothing.
- [ ] `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android` exits 0.

**Steps:** (no committed test: spec verifies the daily check by a device run, Task 7 D6)

- [ ] **Step 1: Prefs.** In `android/app/src/main/kotlin/io/github/jhangyu/relume/api/Prefs.kt`:
  - `enum class CheckInterval { OFF, H1, H6, H24, DAILY }`
  - after `checkInterval` add:

```kotlin
    /** Time of [CheckInterval.DAILY], minutes after local midnight (spec install-states 4). */
    var dailyCheckMinute: Int
        get() = prefs.getInt(KEY_DAILY_CHECK_MINUTE, DEFAULT_DAILY_CHECK_MINUTE)
        set(value) {
            require(value in 0 until MINUTES_PER_DAY) { "daily check time: expected 0..${MINUTES_PER_DAY - 1} minutes after midnight, got $value" }
            save { putInt(KEY_DAILY_CHECK_MINUTE, value) }
        }
```

  - in the companion add:

```kotlin
        const val KEY_DAILY_CHECK_MINUTE = "daily_check_minute"
        const val MINUTES_PER_DAY = 24 * 60
        /** Spec install-states 4: 03:15 until the user picks another time. */
        const val DEFAULT_DAILY_CHECK_MINUTE = 3 * 60 + 15
```

- [ ] **Step 2: Scheduler.** Replace `android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckScheduler.kt` `object CheckScheduler { … }` members `HOURS` and `apply` (keep `WORK_NAME`, `PREFS`, `KEY_LAST`, `outcome`, `lastCheck`, `load`, `record`, `prefs`) with:

```kotlin
    private val HOURS = mapOf(CheckInterval.H1 to 1L, CheckInterval.H6 to 6L, CheckInterval.H24 to 24L)
    private const val DAILY_HOURS = 24L

    /**
     * (Re)schedules unique periodic work `check`. Replaces pending work, so a new interval or daily time takes effect now.
     * Daily: every 24 h from the next chosen local time, only while charging on an unmetered network (spec install-states 4;
     * approximate, Doze maintenance windows apply, no exact alarm).
     */
    fun apply(context: Context, interval: CheckInterval) {
        load(context)
        val wm = WorkManager.getInstance(context)
        val request = when (interval) {
            CheckInterval.OFF -> {
                wm.cancelUniqueWork(WORK_NAME)
                return
            }
            CheckInterval.DAILY -> {
                val minute = (context.applicationContext as RelumeApp).prefs.dailyCheckMinute
                PeriodicWorkRequestBuilder<CheckWorker>(DAILY_HOURS, TimeUnit.HOURS)
                    .setInitialDelay(delayUntil(ZonedDateTime.now(), minute))
                    .setConstraints(
                        Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).setRequiresCharging(true).build(),
                    )
                    .build()
            }
            CheckInterval.H1, CheckInterval.H6, CheckInterval.H24 ->
                PeriodicWorkRequestBuilder<CheckWorker>(checkNotNull(HOURS[interval]) { "check interval $interval has no hours" }, TimeUnit.HOURS)
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                    .build()
        }
        wm.enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.CANCEL_AND_REENQUEUE, request)
    }

    /** From [now] to the next local [minuteOfDay]: today if still ahead, else tomorrow. The clock is a parameter. */
    internal fun delayUntil(now: ZonedDateTime, minuteOfDay: Int): Duration {
        val today = now.with(LocalTime.of(minuteOfDay / 60, minuteOfDay % 60))
        return Duration.between(now, if (today.isAfter(now)) today else today.plusDays(1))
    }
```

  Add imports `io.github.jhangyu.relume.RelumeApp`, `java.time.Duration`, `java.time.LocalTime`, `java.time.ZonedDateTime`. (`PeriodicWorkRequestBuilder<W>(Long, TimeUnit)` is the work-runtime-ktx inline already used here; `setInitialDelay(java.time.Duration)` exists in work-runtime 2.12.0.)

- [ ] **Step 3: Worker message.** In `sync/CheckWorker.kt:40` change `"…, reset it on the Install screen"` to `"…, reset it in the install sheet (start any install on the Apps tab)"`.

- [ ] **Step 4: Settings UI.** In `ui/SettingsScreen.kt`:
  - imports: add `androidx.compose.material3.ExperimentalMaterial3Api`, `androidx.compose.material3.TimeInput`, `androidx.compose.material3.rememberTimePickerState`, `androidx.compose.runtime.mutableIntStateOf`.
  - replace `private val INTERVAL_LABELS = …` with:

```kotlin
private val INTERVALS = listOf(CheckInterval.OFF, CheckInterval.H1, CheckInterval.H6, CheckInterval.H24, CheckInterval.DAILY)

private fun clock(minute: Int) = "%02d:%02d".format(minute / 60, minute % 60)

private fun intervalLabel(i: CheckInterval, dailyMinute: Int) = when (i) {
    CheckInterval.OFF -> "Off"
    CheckInterval.H1 -> "1 h"
    CheckInterval.H6 -> "6 h"
    CheckInterval.H24 -> "24 h"
    CheckInterval.DAILY -> "Daily at ${clock(dailyMinute)}"
}
```

  - in `SettingsScreen`, after `var interval by remember { … }` add `var dailyMinute by remember { mutableIntStateOf(prefs.dailyCheckMinute) }` and `var pickTime by remember { mutableStateOf(false) }`.
  - replace the `ChipRow(INTERVAL_LABELS…) { … }` call in the Background section with:

```kotlin
                ChipRow(INTERVALS.map { intervalLabel(it, dailyMinute) }, INTERVALS.indexOf(interval)) { i ->
                    val value = INTERVALS[i]
                    interval = value
                    prefs.checkInterval = value
                    CheckScheduler.apply(ctx, value)
                    if (value != CheckInterval.OFF && !notificationsOn) askNotifications()
                }
                if (interval == CheckInterval.DAILY) {
                    Hairline()
                    ListRow("Check time", Modifier.clickable { pickTime = true }, supporting = "${clock(dailyMinute)} · while charging on unmetered Wi-Fi")
                }
```

  - at the end of `SettingsScreen`, after the `if (editUrl) ServerUrlDialog(…) { … }` block, add:

```kotlin
    if (pickTime) DailyTimeDialog(dailyMinute, onDismiss = { pickTime = false }) { m ->
        pickTime = false
        dailyMinute = m
        prefs.dailyCheckMinute = m
        CheckScheduler.apply(ctx, CheckInterval.DAILY)
    }
```

  - add at file end:

```kotlin
/** "Daily at" time picker (spec install-states 4): 24 h text input, the check runs near this time. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DailyTimeDialog(minute: Int, onDismiss: () -> Unit, onPick: (Int) -> Unit) {
    val state = rememberTimePickerState(minute / 60, minute % 60, true)
    RDialog(onDismiss) {
        Txt("Daily check time", style = MaterialTheme.typography.headlineSmall)
        TimeInput(state)
        Txt("Android runs the check near this time, while the phone charges on an unmetered network.", color = muted())
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Save", { onPick(state.hour * 60 + state.minute) }, Modifier.weight(1f))
        }
    }
}
```

- [ ] **Step 5: Build and greps.**

Run: `grep -n "DAILY" android/app/src/main/kotlin/io/github/jhangyu/relume/api/Prefs.kt android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckScheduler.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/SettingsScreen.kt; grep -rn "SCHEDULE_EXACT_ALARM\|AlarmManager" android/app/src/main`
Expected: lines in all three files from the first grep, nothing from the second.

Run: `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android; echo RC=$?`
Expected: `RC=0`.

- [ ] **Step 6: Commit.**

```bash
git rev-parse --abbrev-ref HEAD
git add android/app/src/main/kotlin/io/github/jhangyu/relume/api/Prefs.kt android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckScheduler.kt android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckWorker.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/SettingsScreen.kt
git commit -m "feat(android): daily background check at a chosen time, charging and unmetered" -- android/app/src/main/kotlin/io/github/jhangyu/relume/api/Prefs.kt android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckScheduler.kt android/app/src/main/kotlin/io/github/jhangyu/relume/sync/CheckWorker.kt android/app/src/main/kotlin/io/github/jhangyu/relume/ui/SettingsScreen.kt
git status --porcelain -- android
```

### Task 7: Gates and the device run (group E)

**Files:**
- Create: `docs/logs/2026-10-28-install-states-device-run.md` (result page: command, self-captured RC, decisive output line, commit hash per check)

**Interfaces:**
- Consumes: all of Tasks 1–6 committed on `main`; a phone over ADB (serial `10AFCF269C007FB` from the polish batch; confirm with `adb devices`); a Relume server running the Task 1 code (see Behavior).
- Produces: the result page only.

**Behavior:**
- Full suite on macOS and the Linux gate, then build the release APK and install it on the phone.
- The phone must talk to a server with Task 1. The live stack runs from a released image the user publishes (`release.py`); if the live server is older, this task stops with `BLOCKED: server with Task 1 not deployed` and asks the lead (do not deploy or release yourself).
- Device checks (one real run each): (D1) a not-installed app shows Install and installs through the lightbox, which closes itself; (D2) a renamed-package app (patched Google Maps or YouTube with a rename patch) shows "Installed" after install, not Install; (D3) a failure keeps the lightbox open with Retry / Close (turn off "Install unknown apps" for Relume or pick an app whose plan step is an error); (D4) an app installed with another signer shows Uninstall & reinstall → confirm sheet → system uninstall → automatic install → sheet closes; (D5) the same with Cancel in the system prompt → sheet closes, card still shows Uninstall & reinstall; (D6) Daily at 03:15 is scheduled with charging + unmetered and a delay to the next 03:15 (`adb shell dumpsys jobscheduler`).

**Constraints:**
- Do not open `local/data/webui.db` with sqlite3 from the host.
- No test is added; nothing in `android/` or `local/` changes in this task.

**Acceptance criteria:**
- [ ] `python3 scripts/test.py` exits 0 (with `ANDROID_HOME` set) and the Linux gate command from `CLAUDE.md` exits 0; both RCs and their summary lines are in the result page.
- [ ] The result page has one entry D1–D6, each with the observed result (pass/fail) and the evidence line (logcat, dumpsys or the observed screen state).

**Steps:**

- [ ] **Step 1: Full suite (macOS) and Linux gate.** Write each command's output to a file in `docs/logs/` scratch space and capture RC right after the command:

```bash
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py > docs/logs/2026-10-28-install-states-suite.log 2>&1; echo "RC=$?" >> docs/logs/2026-10-28-install-states-suite.log
docker build -f local/Dockerfile --target app -t relume-gate . > docs/logs/2026-10-28-install-states-linux.log 2>&1 && docker run --rm -v "$PWD:/repo" -w /repo relume-gate env -u RELUME_VERSION python3 scripts/test.py --legs python,shell >> docs/logs/2026-10-28-install-states-linux.log 2>&1; echo "RC=$?" >> docs/logs/2026-10-28-install-states-linux.log
git rev-parse HEAD
```

Expected: both logs end with `RC=0`; the suite summary reports 0 failures and total time ≤ 90 s.

- [ ] **Step 2: Server check.** Ask the lead which server the phone uses and whether it runs commit ≥ Task 1. Probe from the phone's point of view: `curl -s -H "Authorization: Bearer <device token>" <server>/api/apps | python3 -c "import json,sys; print('build' in json.load(sys.stdin)['apps'][0])"` (the lead supplies URL and token; do not read the token from the phone). `True` → continue; `False` → report `BLOCKED: server with Task 1 not deployed`.

- [ ] **Step 3: Build and install the APK.**

```bash
adb devices
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools ./android/gradlew -p android assembleRelease --console=plain; echo RC=$?
ls android/app/build/outputs/apk/release/
adb -s 10AFCF269C007FB install -r android/app/build/outputs/apk/release/<the .apk listed above>; echo RC=$?
```

Expected: `RC=0` twice, `Success` from adb.

- [ ] **Step 4: Device checks.** Run `adb -s 10AFCF269C007FB logcat -c` before each, then `adb -s 10AFCF269C007FB logcat -d -s InstallRunner AndroidRuntime > docs/logs/2026-10-28-install-states-D<n>.log` after it. Record in the result page: what you did, what the screen showed, the decisive log line.
  - D1: an app the phone does not have → card shows **Install <version>** → tap → bottom sheet with download progress and phase rail → system confirm if asked → sheet closes by itself → card shows "Installed <version>".
  - D2: an app built with a package-rename patch → after install the card shows "Installed", not Install (`adb shell pm list packages | grep <renamed package>` proves the renamed package is the one installed).
  - D3: turn off "Install unknown apps" for Relume in Android settings while a run is pending, or use an app whose plan step is an error → sheet stays open with "Install failed", the error banner, Close and Retry. Turn the setting back on, Retry → succeeds and closes.
  - D4: an app installed with another signer (e.g. the store version) → card shows "Different signer" + **Uninstall & reinstall** → confirm sheet text says data and login are erased → **Erase and reinstall** → system uninstall prompt → OK → install starts by itself → sheet closes → card "Installed".
  - D5: same as D4 but tap Cancel in the system uninstall prompt → sheet closes, card still "Uninstall & reinstall", app still installed (`adb shell pm list packages | grep <package>`).
  - D6: Settings → Background → **Daily at 03:15** → `adb -s 10AFCF269C007FB shell dumpsys jobscheduler io.github.jhangyu.relume > docs/logs/2026-10-28-install-states-D6.log` → the WorkManager job for `CheckWorker` lists `CHARGING` and `UNMETERED` and a delay ending at the next 03:15. Change the time to 03:30, dump again → delay moved.
  - After D6, set the interval back to what it was before the test (do not change any account or password).

- [ ] **Step 5: Result page.** Write `docs/logs/2026-10-28-install-states-device-run.md`: commit hash, Step 1 commands + RC + summary line, D1–D6 each with pass/fail and the evidence line. Do not commit it (scratch; the lead decides). Report the page path.
