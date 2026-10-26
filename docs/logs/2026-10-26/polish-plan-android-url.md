# Polish batch req 8: Android Settings changes the server URL — implementation plan

Base: `main` @ 3abe409. `K/` = `android/app/src/main/kotlin/io/github/jhangyu/relume/`.
Spec 8: Settings "Server URL" row opens a dialog to edit the URL. Before saving, the app calls `/api/health` on the new URL and checks that it is reachable and compatible. On failure it shows the error and does not save. On success it saves, clears the login state and returns to the login screen.

## Decisions

1. **"Compatible" = `ok == true` and `version` is `dev` or a `MAJOR.MINOR.PATCH` (optional leading `v`) at or above `0.0.4`.**
   - `/api/health` (`local/webui/settings.py:137`) returns `{ok: true, app: "Relume", version: server.VERSION, started_at}`. `server.VERSION` = env `RELUME_VERSION`, default `dev` (`local/webui/server.py:15`). Releases set it to `MAJOR.MINOR.PATCH` (`scripts/release.py:31,132`).
   - Version check, not a new API marker: no backend change (backend files belong to other planners), and old servers (0.0.1–0.0.3) are recognisable today by their version. A marker would need a server change and still not detect old servers better.
   - `dev` passes: it is the local/gate build of the current tree (`local/docker-compose.yml:56` default, Linux gate runs `env -u RELUME_VERSION`). Rejecting it would lock the developer out.
   - Any other string (unparsable, wrong part count) = incompatible. `app` is not checked: a non-Relume server almost never returns `ok`+`version` JSON, and if it does not, decoding already fails with `ApiError("unreadable response")`.
   - Incompatible is reported as `ApiError(200, "server runs <v>, this app needs 0.0.4 or newer", "/api/health")` (resp. `"server reports ok=false"`). Reason: `ApiError` already carries a non-HTTP failure with a success status (`RelumeApi.kt:230-233`), and `describe()` (`K/ui/Errors.kt:7`) and the login catch blocks already handle it. No new exception type, no `Errors.kt` change.
2. **`RelumeApi` gets the new URL by being dropped and rebuilt, no per-request prefs read.** The only signed-in instance is `RelumeApp.api`; a URL change always goes through `signOutLocally` (`api = null`), and the next login builds a new instance in `RelumeApp.signIn` (`RelumeApp.kt:29-33`). Workers read `RelumeApp.api` (null while signed out). So no `RelumeApi`/`RelumeApp` change is needed.
3. **One shared check.** URL parsing is already shared (`ServerUrl.parse`). The health + compatibility step becomes one `RelumeApi` member `checkServer()`, used by Login and Settings. Login stops calling `health()` directly.
4. **Local sign-out only, no call to the old server's `/api/me/logout`.** The main reason to change the URL is that the old server moved or is gone; the existing `logout()` comment (`AppModel.kt`, "a phone whose server moved could never sign in again") follows the same rule. The old token stays valid on the old server until revoked there (open decision below).
5. Dialog: Material3 `AlertDialog` (lead instruction) with the existing `Field` (`Components.kt:693`, an `OutlinedTextField` wrapper) and `ActionButton`. Dialog state lives in `SettingsScreen.kt` with `rememberCoroutineScope`, like `LoginScreen`. No `AppModel` change.

## Edits

### E1 `K/api/RelumeApi.kt` (owner: this plan; not shared)
Region: directly after `suspend fun health(): Health = get("/api/health")` (`:26`), plus a top-level function and constants at the end of the file.

```kotlin
    /** [health] plus the compatibility rule: throws [ApiError] (status 200, path /api/health) when the server is too old or not ok; [java.io.IOException] when unreachable. */
    suspend fun checkServer(): Health {
        val h = health()
        if (!h.ok) throw ApiError(HEALTH_OK, "server reports ok=false", HEALTH_PATH)
        if (!isCompatibleServer(h.version)) throw ApiError(HEALTH_OK, "server runs ${h.version}, this app needs ${MIN_SERVER.joinToString(".")} or newer", HEALTH_PATH)
        return h
    }
```
Change `health()` to `get(HEALTH_PATH)`.

End of file (top level):
```kotlin
private const val HEALTH_PATH = "/api/health"
private const val HEALTH_OK = 200
/** Oldest server whose API this app speaks: 0.0.4 removed the old app routes. */
private val MIN_SERVER = listOf(0, 0, 4)
/** `RELUME_VERSION` unset: a build of the current tree (local stack, Linux gate). */
private const val DEV_VERSION = "dev"

/** [version] from `/api/health`: `dev`, or MAJOR.MINOR.PATCH (optional `v`) not below [MIN_SERVER]. */
internal fun isCompatibleServer(version: String): Boolean {
    if (version == DEV_VERSION) return true
    val parts = version.removePrefix("v").split('.').map { it.toIntOrNull() ?: return false }
    if (parts.size != MIN_SERVER.size) return false
    for (i in parts.indices) if (parts[i] != MIN_SERVER[i]) return parts[i] > MIN_SERVER[i]
    return true
}
```
No 200 constant exists (`HTTP_SUCCESS` is the range `200..299` at `:304`), so `HEALTH_OK` is new.

### E2 `K/ui/LoginScreen.kt` (not shared)
Region `submit()` `:108-114`: replace `api.health()` with `api.checkServer()`. Keep the `catch (e: IOException)` (unreachable banner with Retry). An incompatible server throws `ApiError`, which the outer `catch (e: ApiError)` `:128` shows as the "Sign-in failed" banner with the reason. No other change.

### E3 `K/ui/SettingsScreen.kt` (not shared)
1. Row `:46`: `ListRow("Server URL", Modifier.clickable { editUrl = true }, supporting = prefs.serverUrl?.toString().orEmpty())`. Add `var editUrl by remember { mutableStateOf(false) }` next to `interval` `:32`.
2. After the cleanup dialog block (`:109-121`), inside `SettingsScreen`:
```kotlin
    if (editUrl) ServerUrlDialog(prefs.serverUrl, onDismiss = { editUrl = false }) { url ->
        prefs.serverUrl = url
        editUrl = false
        model.signOutLocally("Server changed to $url. Sign in again.")
    }
```
   `signOutLocally` clears the token and all model state; `api == null` makes `RelumeRoot.kt:102` show `LoginScreen`, which prefills from `prefs.serverUrl` (`LoginScreen.kt:71`, fresh composition) = the new URL. The notice shows as the Warn banner `LoginScreen.kt:154`.
3. New private composable in the same file:
```kotlin
@Composable
private fun ServerUrlDialog(current: ServerUrl?, onDismiss: () -> Unit, onSaved: (ServerUrl) -> Unit) {
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf(current?.toString().orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    fun save() {
        error = null
        val url = try { ServerUrl.parse(text) } catch (e: InvalidServerUrl) { error = e.message; return }
        scope.launch {
            busy = true
            try {
                RelumeApi(url) { null }.checkServer()
                onSaved(url)
            } catch (e: ApiError) {
                error = describe(e)
            } catch (e: IOException) {
                error = "Can't reach $url: ${describe(e)}"
            } finally {
                busy = false
            }
        }
    }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("Server URL") },
        text = {
            Field("Server URL", text, { text = it }, mono = true, error = error, enabled = !busy,
                hint = "Checked with /api/health before saving. Saving signs this phone out.",
                keyboard = KeyboardOptions(keyboardType = KeyboardType.Uri))
        },
        confirmButton = { ActionButton("Save", ::save, kind = ButtonKind.Text, enabled = !busy && text.isNotBlank() && text.trim() != current?.toString()) },
        dismissButton = { ActionButton("Cancel", onDismiss, kind = ButtonKind.Text, enabled = !busy) },
    )
}
```
   Imports to add: `androidx.compose.foundation.clickable`, `androidx.compose.foundation.text.KeyboardOptions`, `androidx.compose.material3.AlertDialog`, `androidx.compose.material3.Text`, `androidx.compose.runtime.rememberCoroutineScope`, `androidx.compose.ui.text.input.KeyboardType`, `io.github.jhangyu.relume.api.{ApiError, InvalidServerUrl, RelumeApi, ServerUrl}`, `java.io.IOException`, `kotlinx.coroutines.launch`.
   - Dismiss is blocked while the check runs, so a late success cannot save after the user closed the dialog.
   - Save is disabled when the text equals the current URL (no forced re-login for a no-op).
   - Settings stays composed until `onSaved` runs; `onSaved` runs on the main thread (scope is the composition scope).

### E4 test `android/app/src/test/kotlin/io/github/jhangyu/relume/api/ServerCompatTest.kt` (new)
The compatibility rule is a parser + comparison with no test and decides whether a phone can connect: core logic, so one small JUnit4 test:
- accepted: `dev`, `0.0.4`, `v0.0.4`, `0.0.10`, `0.1.0`, `1.0.0`
- rejected: `0.0.3`, `0.0.1`, `v0.0.3`, `0.0`, `0.0.4.1`, `0.0.4-rc1`, `""`, `latest`

## Files touched
- `K/api/RelumeApi.kt`: `health()` line `:26` + new `checkServer()` after it; top-level additions at end of file.
- `K/ui/LoginScreen.kt`: one line in `submit()` (`:110`).
- `K/ui/SettingsScreen.kt`: `:32` (state), `:46` (row), new block after `:121`, new private composable at file end, imports.
- `android/app/src/test/kotlin/io/github/jhangyu/relume/api/ServerCompatTest.kt`: new.
- NOT touched: `AppsScreen.kt`, `Models.kt`, `AppModel.kt` (owned by the Followed-app cards implementer), `RelumeApp.kt`, `Prefs.kt`, `Errors.kt`, `Components.kt`, `RelumeRoot.kt`. No overlap with the other Android plan.

## Tests that must change
None. `ServerUrlTest` covers `ServerUrl.parse`, which is unchanged. `RedirectTest.kt:20,29` calls `api.health()` against a local server; `health()` keeps its behaviour, so it stays unchanged. No UI tests exist.

## Acceptance checks (mechanical)
1. `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools android/gradlew -p android :app:assembleDebug --console=plain --no-watch-fs; RC=$?` → `RC=0`.
2. `ANDROID_HOME=/opt/homebrew/share/android-commandlinetools python3 scripts/test.py --legs android; RC=$?` → `RC=0`, output lists `ServerCompatTest` with 0 failures, 0 skipped.
3. `grep -n "\.health()" android/app/src/main/kotlin/io/github/jhangyu/relume/ui/LoginScreen.kt` → no match; `grep -c "checkServer()" K/ui/LoginScreen.kt K/ui/SettingsScreen.kt` → 1 each.
4. `grep -n "clickable" K/ui/SettingsScreen.kt` → the Server URL row; `grep -n "AlertDialog(" K/ui/SettingsScreen.kt` → 1 match.
5. `git diff --name-only 3abe409 -- android/` lists only the four files above (plus the other plan's files).
6. On device (user, not automated): enter a 0.0.3 or unreachable URL → error in the dialog, URL unchanged; enter the real 0.0.4+ URL → login screen with the new URL prefilled and the "Server changed" notice.

## Open decisions
- Old token is not revoked on the old server (local sign-out only). Revoking first would block the change when the old server is gone. Alternative: try `/api/me/logout` on the old server and ignore failure — rejected as a silent fallback. User can revoke from the old server's Devices page.

## Uncertain
- `ActionButton` inside `AlertDialog` slots: assumed to compose fine (it wraps Material3 `TextButton` for `ButtonKind.Text`, `Components.kt:347-390`).
