package io.github.jhangyu.relume.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.jhangyu.relume.BuildConfig
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.Artifact
import io.github.jhangyu.relume.api.Catalog
import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.Health
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.CheckInterval
import io.github.jhangyu.relume.api.Item
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
    var catalog by mutableStateOf<Catalog?>(null)
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

    /** Bumped when any build finished, so the Builds list and detail pages reload. */
    var buildsVersion by mutableStateOf(0)
        private set

    /** Job ids with an open event stream; a stream ends on `event: end` or on its first error. */
    private val watching = mutableMapOf<Int, Job>()

    /** Spec 7.4: the server offers a newer companion than this build. */
    val versionGate: Pair<Long, Long>?
        get() {
            val offered = health?.companionVersionCode ?: return null
            val own = BuildConfig.VERSION_CODE.toLong()
            return if (offered > own) offered to own else null
        }

    val items: List<Item>
        get() = catalog?.let { c -> c.groups.flatMap { it.items } + c.ungrouped }.orEmpty()

    fun item(id: Int): Item? = items.firstOrNull { it.id == id }

    fun facts(item: Item): AppFacts =
        AppFacts(item, installed[item.packageName], plan?.steps?.firstOrNull { it.refId == item.id && it.kind == StepKind.ITEM })

    fun signedIn() {
        api = relume.api
        notice = null
        refresh()
    }

    fun signOutLocally(reason: String?) {
        relume.signOut()
        api = null
        notice = reason
        catalog = null
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
        if (versionGate != null) {
            loadDeviceState()
            return
        }
        catalog = call { it.catalog() } ?: return
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
                call { it.putAssignments(d.id, d.assignments.map { a -> a.itemId } + f.item.id, null) } ?: return@launch
            }
            val scoped = call { it.plan(listOf(f.item.id), null) } ?: return@launch
            evaluate(f.item.id, scoped)
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

    /** Spec 7.4: runs the plan's companion step. */
    fun updateCompanion() {
        val steps = plan?.steps?.filter { it.kind == StepKind.COMPANION }.orEmpty()
        if (steps.isEmpty()) {
            error = "companion update: the server offers version ${versionGate?.first} but its plan has no companion step"
            return
        }
        start(InstallPlan(checkNotNull(plan).revision, steps))
    }

    private fun start(p: InstallPlan) {
        when (InstallRunner.start(relume, p, Origin.UI)) {
            StartResult.Busy -> error = "an install run is in progress"
            // Corrupt: the Install screen shows the unreadable run file and its reset.
            StartResult.Started, StartResult.Corrupt -> runStarted++
        }
    }

    fun loadArtifacts(itemId: Int, into: (List<Artifact>) -> Unit) {
        viewModelScope.launch { call { it.artifacts(itemId) }?.let(into) }
    }

    fun setSwitches(itemId: Int, enabled: Boolean?, autoUpdate: Boolean?) {
        viewModelScope.launch {
            val updated = call { it.setSwitches(itemId, enabled, autoUpdate) } ?: return@launch
            catalog = catalog?.replacing(updated)
        }
    }

    /** "Build now": queues a build and opens its stream. */
    fun build(itemId: Int, opened: (Int) -> Unit) {
        viewModelScope.launch { call { it.build(itemId) }?.let { opened(it); refresh() } }
    }

    /** "Check upstream": spec 6.7 per-item update trigger, no rebuild forced. */
    fun checkUpstream(itemId: Int) {
        viewModelScope.launch {
            call { it.update(itemId, rebuild = false) }
            refresh()
        }
    }

    fun checkAll() {
        viewModelScope.launch {
            call { it.checkAll() }
            refresh()
        }
    }

    /** One stream per job seen active; on its end event the item and plan are refreshed (spec 10, no polling). */
    private fun watchActiveJobs() {
        val a = api ?: return
        items.mapNotNull { it.activeJob?.jobId }.filter { it !in watching }.forEach { jobId ->
            watching[jobId] = viewModelScope.launch {
                try {
                    withContext(Dispatchers.IO) { a.jobLog(jobId, null) { } }
                    buildsVersion++
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
        buildsVersion++
        refresh()
    }
}

internal fun Catalog.replacing(updated: Item): Catalog = Catalog(
    groups = groups.map { g -> io.github.jhangyu.relume.api.Group(g.id, g.name, g.color, g.items.map { if (it.id == updated.id) updated else it }) },
    ungrouped = ungrouped.map { if (it.id == updated.id) updated else it },
)
