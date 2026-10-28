package io.github.jhangyu.relume.install

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.inventory.Inventory
import io.github.jhangyu.relume.sync.CheckScheduler
import io.github.jhangyu.relume.sync.Notifications
import kotlinx.coroutines.flow.StateFlow
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

/** Entry point of the install pipeline for the UI and the background check (spec 9). */
object InstallRunner {
    private const val TAG = "InstallRunner"

    val state: StateFlow<RunState?> = InstallRunStore.state

    /** The run file could not be read; [start] refuses until the user calls [reset]. */
    val corrupt: StateFlow<CorruptRunFile?> = InstallRunStore.corrupt

    /** Confirm intents (`EXTRA_INTENT`) by step index; process-local, they cannot be persisted. */
    private val confirmIntents = ConcurrentHashMap<Int, Intent>()
    private val reconciled = AtomicBoolean(false)

    /**
     * Starts [plan] unless a step of the current run is downloading, verifying, committed or awaiting the user, or the
     * run file is corrupt. Unsent receipts of the replaced run go with the new run.
     */
    fun start(context: Context, plan: InstallPlan, origin: Origin): StartResult {
        InstallRunStore.load(context)
        if (InstallRunStore.corrupt.value != null) return StartResult.Corrupt
        var started = false
        InstallRunStore.update(context) { cur ->
            if (cur?.isActive == true) cur else InstallRun.create(plan, origin, cur).also { started = true }
        }
        if (!started) return StartResult.Busy
        confirmIntents.clear()
        InstallWorker.enqueue(context, origin, replace = true)
        return StartResult.Started
    }

    /** "Confirm" for an `awaiting_user` step: shows the system prompt; after process death the step is re-run. */
    fun confirm(context: Context, stepIndex: Int) {
        val s = stepAt(stepIndex, StepState.AWAITING_USER)
        val intent = confirmIntents.remove(stepIndex)
        if (intent != null) {
            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        // ponytail: a lost prompt re-runs the step (new download + session); keep the verified file if that cost matters.
        s.sessionId?.let { if (Installer.sessionExists(context, it)) Installer.abandon(context, it) }
        retry(context, stepIndex)
    }

    /** `needs_uninstall` step: system uninstall confirmation, then the step is re-run. */
    fun uninstallAndRetry(context: Context, stepIndex: Int) {
        val s = stepAt(stepIndex, StepState.NEEDS_UNINSTALL)
        Installer.uninstall(context, checkNotNull(InstallRunStore.state.value).revision, s.step.packageName, stepIndex)
    }

    /** Cancels the run: open sessions are abandoned, unfinished steps end `cancelled`, receipts are still sent. */
    fun cancel(context: Context) {
        val before = InstallRunStore.state.value ?: return
        checkNotNull(InstallRunStore.update(context) { cur -> cur?.let(InstallRun::cancelAll) })
        before.steps.forEachIndexed { i, s ->
            val id = s.sessionId
            if (s.state.active && id != null && Installer.sessionExists(context, id)) Installer.abandon(context, id)
            Notifications.cancelStep(context, i)
        }
        confirmIntents.clear()
        // User action: only the receipts are left to send, so no background constraints.
        InstallWorker.enqueue(context, Origin.UI, replace = true)
    }

    /** The user's reset of a corrupt run file (spec 9.1): the file is deleted, the next start begins empty. */
    fun reset(context: Context) = InstallRunStore.reset(context)

    /**
     * Call at process start (MainActivity.onCreate; later calls in the same process do nothing). Loads the run file and the last check outcome, abandons
     * sessions no step references, and resolves committed / awaiting steps whose session is gone (spec 9.4).
     */
    fun reconcileOnStart(context: Context) {
        if (!reconciled.compareAndSet(false, true)) return
        InstallRunStore.load(context)
        CheckScheduler.load(context)
        val run = InstallRunStore.state.value ?: return
        val live = run.steps.mapNotNull { it.sessionId }.toSet()
        Installer.abandonOwnSessionsExcept(context, live)
        val after = InstallRunStore.update(context) { cur ->
            checkNotNull(cur).steps.indices.fold(cur) { acc, i ->
                val s = acc.steps[i]
                val id = s.sessionId
                val waiting = s.state == StepState.COMMITTED || s.state == StepState.AWAITING_USER
                if (waiting && (id == null || !Installer.sessionExists(context, id))) InstallRun.finish(acc, i, lostSession(context, s)) else acc
            }
        }
        after?.steps?.forEachIndexed { i, s -> if (s.state == StepState.INSTALLED && run.steps[i].state != StepState.INSTALLED) recordPatch(context, s.step) }
        if (after != run) InstallWorker.enqueue(context, run.passOrigin, replace = false)
    }

    /** Reads the run file without reconciling sessions (for the background check). */
    internal fun loadState(context: Context) = InstallRunStore.load(context)

    fun canInstall(context: Context): Boolean = context.packageManager.canRequestPackageInstalls()

    internal val isActive get() = InstallRunStore.state.value?.isActive == true

    internal fun onSessionResult(context: Context, sessionId: Int, status: Int, message: String?, confirm: Intent?) {
        InstallRunStore.load(context)
        val run = InstallRunStore.state.value
        val index = run?.indexOfSession(sessionId)
        if (run == null || index == null) {
            Log.w(TAG, "session $sessionId result ${InstallResult.statusName(status)}: no committed step has this session (stale result)")
            return
        }
        val outcome = InstallResult.from(status, message)
        val name = run.steps[index].step.displayName
        if (outcome.state == StepState.AWAITING_USER) {
            InstallRunStore.update(context) { cur -> cur?.let { InstallRun.move(it, index, setOf(StepState.COMMITTED, StepState.AWAITING_USER), StepState.AWAITING_USER) } ?: cur }
            prompt(context, index, name, checkNotNull(confirm) { "session $sessionId: STATUS_PENDING_USER_ACTION without EXTRA_INTENT" })
            return
        }
        confirmIntents.remove(index)
        Notifications.cancelStep(context, index)
        val after = InstallRunStore.update(context) { cur -> cur?.let { InstallRun.finish(it, index, outcome) } }
        if (after?.steps?.get(index)?.state == StepState.INSTALLED) recordPatch(context, run.steps[index].step)
        if (outcome.state == StepState.NEEDS_UNINSTALL && run.origin == Origin.BACKGROUND) Notifications.needsReinstall(context, index, name)
        InstallWorker.enqueue(context, run.passOrigin, replace = false)
    }

    internal fun onUninstallResult(
        context: Context, revision: String, stepIndex: Int, packageName: String, status: Int, message: String?, confirm: Intent?,
    ) {
        InstallRunStore.load(context)
        InstallRun.staleUninstall(InstallRunStore.state.value, revision, stepIndex, packageName)?.let {
            Log.w(TAG, "uninstall result ${InstallResult.statusName(status)} for $packageName (step $stepIndex, revision $revision) ignored: $it")
            return
        }
        val s = stepAt(stepIndex, StepState.NEEDS_UNINSTALL)
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION ->
                prompt(context, stepIndex, s.step.displayName, checkNotNull(confirm) { "uninstall of ${s.step.packageName}: no EXTRA_INTENT" })
            PackageInstaller.STATUS_SUCCESS -> retry(context, stepIndex)
            PackageInstaller.STATUS_FAILURE_ABORTED -> InstallRunStore.update(context) { cur ->
                cur?.let { InstallRun.endUninstall(it, stepIndex, StepOutcome(StepState.CANCELLED, ErrorCodes.USER_REJECTED, "uninstall of ${s.step.packageName} cancelled by the user")) }
            }
            else -> {
                val text = "uninstall of ${s.step.packageName} failed: ${InstallResult.statusName(status)}" + (message?.let { ": $it" } ?: "")
                InstallRunStore.update(context) { cur -> cur?.let { InstallRun.endUninstall(it, stepIndex, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, text)) } }
            }
        }
    }

    /** [packageName] is gone from the phone: its `needs_uninstall` step re-runs (spec install-states 3). */
    internal fun onPackageRemoved(context: Context, packageName: String) {
        InstallRunStore.load(context)
        val run = InstallRunStore.state.value ?: return
        val index = run.steps.indexOfFirst { it.state == StepState.NEEDS_UNINSTALL && it.step.packageName == packageName }
        if (index >= 0) retry(context, index)
    }

    private fun prompt(context: Context, index: Int, name: String, confirm: Intent) {
        if (AppVisibility.visible) {
            context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } else {
            confirmIntents[index] = confirm
            Notifications.confirmInstall(context, index, name, confirm)
        }
    }

    /** Records the patch version of a step that ended `installed` under its built APK package (spec install-states 1). */
    private fun recordPatch(context: Context, step: PlanStep) {
        step.download?.patchVersion?.let { InstalledPatches.record(context, step.packageName, it) }
    }

    /** Re-runs step [index] after a user action (confirm, uninstall): network CONNECTED only, even for a background run. */
    private fun retry(context: Context, index: Int) {
        InstallRunStore.update(context) { cur -> cur?.let { InstallRun.retry(it, index) } }
        InstallWorker.enqueue(context, Origin.UI, replace = false)
    }

    private fun stepAt(index: Int, expected: StepState): StepRun {
        val run = checkNotNull(InstallRunStore.state.value) { "install run: no run, expected step $index in state $expected" }
        val s = run.steps.getOrNull(index) ?: throw IndexOutOfBoundsException("install run: step $index of ${run.steps.size}")
        check(s.state == expected) { "install run: step $index (${s.step.displayName}) is ${s.state}, expected $expected" }
        return s
    }

    /** A committed step whose session vanished: installed if the phone now has the target, else `session lost`. */
    private fun lostSession(context: Context, s: StepRun): StepOutcome {
        val d = checkNotNull(s.step.download) { "step ${s.step.displayName}: committed without a download" }
        val done = Inventory.installed(context, s.step.packageName)
            ?.let { it.versionCode == d.versionCode && VerifyRules.hexEquals(it.signerSha256, d.signerSha256) } == true
        return if (done) StepOutcome(StepState.INSTALLED, null, null)
        else StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "session lost")
    }
}
