package io.github.jhangyu.relume.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.AppDetail
import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.BundleOption
import io.github.jhangyu.relume.api.CleanupReport
import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.Health
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.CheckInterval
import io.github.jhangyu.relume.api.OtaSchedule
import io.github.jhangyu.relume.api.Schedule
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.PatchesView
import io.github.jhangyu.relume.api.Project
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.InstallRunner
import io.github.jhangyu.relume.install.Origin
import io.github.jhangyu.relume.install.StartResult
import io.github.jhangyu.relume.inventory.Inventory
import io.github.jhangyu.relume.sync.CheckScheduler
import io.github.jhangyu.relume.api.RelumeApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.IOException

private const val HTTP_UNAUTHORIZED = 401
internal const val SIGNED_OUT_NOTICE = "This device was signed out by the server"

/** Everything the screens show, with the server calls of spec 10. State is Compose state: the UI reads it directly. */
class AppModel(application: Application) : AndroidViewModel(application) {
    val relume = application as RelumeApp

    var api by mutableStateOf(relume.api)
        private set
    var notice by mutableStateOf<String?>(null)
        private set
    var health by mutableStateOf<Health?>(null)
        private set
    var apps by mutableStateOf<List<AppObject>>(emptyList())
        private set
    var schedule by mutableStateOf<Schedule?>(null)
        private set
    var otaSchedules by mutableStateOf<List<OtaSchedule>>(emptyList())
        private set
    var device by mutableStateOf<DeviceView?>(null)
        private set
    var installed by mutableStateOf<Map<String, InstalledPackage>>(emptyMap())
        private set
    var plan by mutableStateOf<InstallPlan?>(null)
        private set
    var refreshing by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set

    /** Job ids with an open event stream; a stream ends on `event: end` or on its first error. */
    private val watching = mutableMapOf<Int, Job>()

    /** Marketplace state; it shares this model's server calls and sign-out handling. */
    val market = MarketModel(this)

    fun app(id: Int): AppObject? = apps.firstOrNull { it.id == id }

    fun facts(app: AppObject): AppFacts =
        AppFacts(app, installed[app.packageName], plan?.steps?.firstOrNull { it.refId == app.id && it.kind == StepKind.ITEM })

    fun signedIn() {
        api = relume.api
        notice = null
        refresh()
    }

    fun signOutLocally(reason: String?) {
        relume.signOut()
        api = null
        notice = reason
        apps = emptyList()
        schedule = null
        otaSchedules = emptyList()
        market.reset()
        device = null
        plan = null
        installed = emptyMap()
        health = null
        CheckScheduler.apply(relume, CheckInterval.OFF)
        watching.values.forEach { it.cancel() }
        watching.clear()
    }

    fun logout() {
        viewModelScope.launch {
            val loggedOut = call { it.logout(); true }
            if (api == null) return@launch // a 401 already signed this phone out
            // Always sign out locally, or a phone whose server moved could never sign in again; a failure is shown on the login screen.
            val failure = error
            error = null
            signOutLocally(
                if (loggedOut == true) null else "Signed out on this phone, but the server was not told ($failure). Revoke this phone on the web Settings page.",
            )
        }
    }

    fun dismissError() {
        error = null
    }

    /** 401 → back to login; every other failure becomes [error] (status + server text + path) and returns null. */
    suspend fun <T> call(block: suspend (RelumeApi) -> T): T? {
        val a = api ?: return null
        error = null
        return try {
            block(a)
        } catch (e: ApiError) {
            if (e.status == HTTP_UNAUTHORIZED) signOutLocally(SIGNED_OUT_NOTICE) else error = describe(e)
            null
        } catch (e: IOException) {
            error = describe(e)
            null
        }
    }

    fun refresh() {
        if (api == null) return
        viewModelScope.launch {
            refreshing = true
            try {
                load()
            } finally {
                refreshing = false
            }
        }
    }

    private suspend fun load() {
        health = call { it.health() } ?: return
        apps = call { it.apps() } ?: return
        schedule = call { it.schedule() } ?: return
        otaSchedules = call { it.otaSchedules() } ?: return
        loadDeviceState()
        watchActiveJobs()
    }

    private suspend fun loadDeviceState() {
        val r = call { Inventory.sync(relume, it) } ?: return
        device = r.device
        plan = r.plan
        installed = r.reported.associateBy { it.packageName }
    }

    // ---- install start (spec 10: plan, assignment, dependency and signer prompts) ----

    /** Question shown before a run starts. */
    sealed interface InstallPrompt {
        data object NeedPermission : InstallPrompt
        class Signer(val itemId: Int, val plan: InstallPlan, val step: PlanStep) : InstallPrompt
        class Dependencies(val plan: InstallPlan) : InstallPrompt
    }

    var prompt by mutableStateOf<InstallPrompt?>(null)
        private set

    /** Bumped when a run starts; the root opens the install screen. */
    var runStarted by mutableStateOf(0)
        private set

    fun dismissPrompt() {
        prompt = null
    }

    fun beginInstall(f: AppFacts) {
        viewModelScope.launch {
            if (!InstallRunner.canInstall(relume)) {
                prompt = InstallPrompt.NeedPermission
                return@launch
            }
            // Fresh assignments: the cached device may predate an install started a moment ago.
            val d = call { it.me().device } ?: return@launch
            val assigned = d.assignments.map { a -> a.itemId }
            if (f.app.id !in assigned) {
                // Spec 8.3: assign to this phone first (projects stay as they are), so OTA keeps it updated too.
                call { it.putAssignments(d.id, assigned + f.app.id, null) } ?: return@launch
            }
            val scoped = call { it.plan(listOf(f.app.id), null) } ?: return@launch
            evaluate(f.app.id, scoped)
        }
    }

    private fun evaluate(itemId: Int, scoped: InstallPlan) {
        scoped.steps.firstOrNull { it.signerChange != null }?.let {
            prompt = InstallPrompt.Signer(itemId, scoped, it)
            return
        }
        if (scoped.steps.count { it.action == PlanAction.INSTALL } > 1) {
            prompt = InstallPrompt.Dependencies(scoped)
            return
        }
        start(scoped)
    }

    fun confirmDependencies(p: InstallPrompt.Dependencies) {
        prompt = null
        start(p.plan)
    }

    /** Spec 10: accepting trusts a new signing key; the plan is fetched again afterwards. */
    fun acceptSigner(p: InstallPrompt.Signer) {
        prompt = null
        val change = checkNotNull(p.step.signerChange) { "signer prompt for step ${p.step.refId} without signer_change" }
        viewModelScope.launch {
            call { it.acceptSigner(p.step.refId, change.offered) } ?: return@launch
            val fresh = call { it.plan(listOf(p.itemId), null) } ?: return@launch
            evaluate(p.itemId, fresh)
        }
    }

    private fun start(p: InstallPlan) {
        when (InstallRunner.start(relume, p, Origin.UI)) {
            StartResult.Busy -> error = "an install run is in progress"
            // Corrupt: the Install screen shows the unreadable run file and its reset.
            StartResult.Started, StartResult.Corrupt -> runStarted++
        }
    }

    /** Disable: the server keeps the row and its settings, the app leaves this list. */
    fun disable(appId: Int, done: () -> Unit) {
        viewModelScope.launch {
            call { it.disable(appId); true } ?: return@launch
            done()
            refresh()
        }
    }

    /** Rebuild: queues a build and opens its stream. */
    fun rebuild(appId: Int, opened: (Int) -> Unit) {
        viewModelScope.launch { call { it.build(appId) }?.let { opened(it); refresh() } }
    }

    /** Everything App settings edits. [bundles] is null when the catalog has no entry for the package (error shown). */
    class SettingsData(val detail: AppDetail, val patches: PatchesView?, val bundles: List<BundleOption>?, val projects: List<Project>)

    /** Null when a required call failed ([error] says which). Module bundles have no patch list (server 409). */
    suspend fun loadSettings(appId: Int): SettingsData? {
        val detail = call { it.app(appId) } ?: return null
        val patches = if (isModule(detail.app.bundle.engine)) null else call { it.patches(appId) } ?: return null
        val projects = call { it.projects() } ?: return null
        val bundles = call { it.marketApp(detail.app.packageName).bundles }
        return SettingsData(detail, patches, bundles, projects)
    }

    /**
     * App settings save: the partial `PUT /api/apps/{id}` body (null = no setting changed), then the patch overrides
     * (null = unchanged), then the rebuild when asked. A failed step stops the rest; [done] gets the build job id.
     */
    fun saveApp(appId: Int, body: JsonObject?, overrides: JsonObject?, rebuildAfter: Boolean, done: (Int?) -> Unit) {
        viewModelScope.launch {
            if (body != null) call { it.putApp(appId, body) } ?: return@launch
            if (overrides != null) call { it.putPatches(appId, buildJsonObject { put("overrides", overrides) }); true } ?: return@launch
            val job = if (rebuildAfter) call { it.build(appId) } ?: return@launch else null
            done(job)
            refresh()
        }
    }

    /** Apps tab "Add URL" / "Pick one": sets the APKMirror listing URL; the server then starts the update run. */
    fun setListUrl(appId: Int, url: String, done: () -> Unit) {
        viewModelScope.launch {
            call { it.putApp(appId, buildJsonObject { put("download", buildJsonObject { put("apkmirror_list_url", url) }) }) } ?: return@launch
            done()
            refresh()
        }
    }

    /** Look the APKMirror page up again (manual retry, spec 5.3). */
    fun locate(appId: Int) {
        viewModelScope.launch {
            call { it.locate(appId) } ?: return@launch
            refresh()
        }
    }

    // ---- OTA schedules (kind ota; phones are ADB devices, the server refuses app devices as targets) ----

    var devices by mutableStateOf<List<DeviceView>>(emptyList())
        private set

    fun loadDevices() {
        viewModelScope.launch { call { it.devices() }?.let { devices = it } }
    }

    /** Creates ([id] null) or replaces an OTA schedule; [done] closes the sheet after success. */
    fun saveOta(id: Int?, body: JsonObject, done: () -> Unit) {
        viewModelScope.launch {
            call { if (id == null) it.createOtaSchedule(body) else it.putOtaSchedule(id, body) } ?: return@launch
            done()
            otaSchedules = call { it.otaSchedules() } ?: return@launch
        }
    }

    fun setOtaEnabled(id: Int, on: Boolean) = saveOta(id, buildJsonObject { put("enabled", on) }) {}

    fun deleteOta(id: Int) {
        viewModelScope.launch {
            call { it.deleteOtaSchedule(id); true } ?: return@launch
            otaSchedules = call { it.otaSchedules() } ?: return@launch
        }
    }

    fun putSchedule(body: JsonObject) {
        viewModelScope.launch { call { it.putSchedule(body) }?.let { schedule = it } }
    }

    fun runSchedule() {
        viewModelScope.launch {
            call { it.runSchedule() }
            refresh()
        }
    }

    /** Settings "Clean up": [cleanupPreview] is the pending confirmation, [cleanupFreed] the result of the last run. */
    var cleanupPreview by mutableStateOf<CleanupReport?>(null)
        private set
    var cleanupFreed by mutableStateOf<CleanupReport?>(null)
        private set

    fun previewCleanup() {
        cleanupFreed = null
        viewModelScope.launch { cleanupPreview = call { it.cleanupPreview() } }
    }

    fun dismissCleanup() {
        cleanupPreview = null
    }

    fun runCleanup() {
        cleanupPreview = null
        viewModelScope.launch { cleanupFreed = call { it.cleanup() } }
    }

    /** Check for an update of one app (the scoped update run). */
    fun check(appId: Int) {
        viewModelScope.launch {
            call { it.check(appId) }
            refresh()
        }
    }

    /** One stream per job seen active; on its end event the item and plan are refreshed (spec 10, no polling). */
    private fun watchActiveJobs() {
        val a = api ?: return
        apps.filter { it.status.state == STATE_BUILDING || it.status.state == STATE_QUEUED }.mapNotNull { it.status.jobId }.filter { it !in watching }.forEach { jobId ->
            watching[jobId] = viewModelScope.launch {
                try {
                    withContext(Dispatchers.IO) { a.jobLog(jobId, null) { } }
                    refresh()
                } catch (e: ApiError) {
                    error = describe(e)
                } catch (e: IOException) {
                    error = describe(e)
                } finally {
                    watching.remove(jobId)
                }
            }
        }
    }

    /** Called by the log screen when its own stream ended. */
    fun buildEnded() {
        refresh()
    }
}

