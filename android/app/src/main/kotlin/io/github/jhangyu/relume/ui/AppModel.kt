package io.github.jhangyu.relume.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.AppObject
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
            val d = device ?: run { error = "install: this phone is not registered with the server yet, refresh and try again"; return@launch }
            if (f.step == null) {
                call { it.putAssignments(d.id, d.assignments.map { a -> a.itemId } + f.app.id, null) } ?: return@launch
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

    /** Saves the partial `PUT /api/apps/{id}` body and, when asked, rebuilds. */
    fun saveApp(appId: Int, body: JsonObject, rebuildAfter: Boolean, done: (Int?) -> Unit) {
        viewModelScope.launch {
            call { it.putApp(appId, body) } ?: return@launch
            val job = if (rebuildAfter) call { it.build(appId) } ?: return@launch else null
            done(job)
            refresh()
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

