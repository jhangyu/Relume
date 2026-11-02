package io.github.jhangyu.relume.install

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.InstallPlan
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

    /** Confirm intents (`EXTRA_INTENT`) by step index, kept until the step's result; process-local, they cannot be persisted. */
    private val confirmIntents = ConcurrentHashMap<Int, Intent>()
    private val reconciled = AtomicBoolean(false)

    /**
     * Starts [plan], or queues it while a step of the current run holds a PackageInstaller session still open in the
     * system or is being downloaded by a pending install worker (the run record alone never blocks, [InstallRun.admit]);
     * a queued plan starts when that step ends and a step awaiting the user gets its kept confirmation shown now (a new
     * start never abandons a session). Unsent receipts of the replaced run go with the new run.
     */
    fun start(context: Context, plan: InstallPlan, origin: Origin): StartResult {
        InstallRunStore.load(context)
        if (InstallRunStore.corrupt.value != null) return StartResult.Corrupt
        // The user asks for every app of a UI plan (the tapped one and the dependencies the sheet listed): none was removed.
        if (origin == Origin.UI) prefs(context).updateSeenInstalled { it - requestedKeys(plan) }
        val before = InstallRunStore.state.value
        var admission: Admission? = null
        // System state read under the store lock: the worker cannot commit a session between the read and the decision.
        InstallRunStore.update(context) { cur ->
            InstallRun.admit(cur, plan, origin, Installer.ownSessions(context), InstallWorker.pending(context)) { lostSession(context, it) }
                .also { admission = it }.run
        }
        val a = checkNotNull(admission)
        if (!a.started) {
            a.surface?.let { confirm(context, it, byUser = false) }
            return StartResult.Queued
        }
        before?.steps?.forEachIndexed { i, s -> if (s.state.active) Notifications.cancelStep(context, i) }
        confirmIntents.clear()
        InstallWorker.enqueue(context, origin, replace = true)
        return StartResult.Started
    }

    /**
     * Shows the system confirmation of `awaiting_user` step [stepIndex] (directly when the app is visible, else as a
     * notification). Its `EXTRA_INTENT` arrives only once, with `STATUS_PENDING_USER_ACTION`; when it is gone (process
     * death, app update) only the user's Confirm ([byUser]) abandons the committed session and re-runs the step, whose
     * new commit brings a fresh confirmation: the session may be installing already ([InstallRun.confirmAction]).
     */
    fun confirm(context: Context, stepIndex: Int, byUser: Boolean = true) {
        val s = stepAt(stepIndex, StepState.AWAITING_USER)
        val intent = confirmIntents[stepIndex]
        when (InstallRun.confirmAction(kept = intent != null, byUser = byUser)) {
            ConfirmAction.SHOW -> prompt(context, stepIndex, s.step.displayName, checkNotNull(intent))
            ConfirmAction.RERUN -> {
                // ponytail: a lost prompt re-runs the step (new download + session); keep the verified file if that cost matters.
                s.sessionId?.let { if (Installer.sessionExists(context, it)) Installer.abandon(context, it) }
                retry(context, stepIndex)
            }
            ConfirmAction.WAIT -> Unit // the install sheet shows Confirm
        }
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
        if (startQueued(context)) return
        // User action: only the receipts are left to send, so no background constraints.
        InstallWorker.enqueue(context, Origin.UI, replace = true)
    }

    /** Starts the queued plan once the step it waits for has ended; true = started. */
    internal fun startQueued(context: Context): Boolean {
        var next: RunState? = null
        InstallRunStore.update(context) { cur -> cur?.let(InstallRun::dequeue)?.also { next = it } ?: cur }
        val run = next ?: return false
        confirmIntents.clear()
        InstallWorker.enqueue(context, run.origin, replace = true)
        return true
    }

    /** The user's reset of a corrupt run file (spec 9.1): the file is deleted, the next start begins empty. */
    fun reset(context: Context) = InstallRunStore.reset(context)

    /**
     * Call at process start (MainActivity.onCreate, CheckWorker; later calls in the same process do nothing). Loads the
     * run file and the last check outcome, abandons sessions no step references (also with no run, e.g. after cleared
     * data) unless an install worker may hold one it has not recorded yet, and ends the steps the system no longer
     * carries ([InstallRun.closeStale], spec 9.4).
     */
    fun reconcileOnStart(context: Context) {
        if (!reconciled.compareAndSet(false, true)) return
        InstallRunStore.load(context)
        CheckScheduler.load(context)
        if (InstallRunStore.corrupt.value != null) return
        val run = InstallRunStore.state.value
        val workPending = InstallWorker.pending(context)
        if (!workPending) Installer.abandonOwnSessionsExcept(context, run?.steps.orEmpty().mapNotNull { it.sessionId }.toSet())
        if (run == null) return
        val after = InstallRunStore.update(context) { cur ->
            InstallRun.closeStale(checkNotNull(cur), Installer.ownSessions(context), workPending) { lostSession(context, it) }
        }
        if (startQueued(context)) return
        if (after != run) InstallWorker.enqueue(context, run.passOrigin, replace = false)
    }

    /** Reads the run file without reconciling sessions (for the background check). */
    internal fun loadState(context: Context) = InstallRunStore.load(context)

    fun canInstall(context: Context): Boolean = context.packageManager.canRequestPackageInstalls()

    /** Ends step [index] with [outcome]. A background step this call moved to `needs_uninstall` posts the needs-reinstall notification. */
    internal fun finishStep(context: Context, index: Int, outcome: StepOutcome): RunState? {
        var entered = false
        val after = InstallRunStore.update(context) { cur ->
            cur?.let { run -> InstallRun.finish(run, index, outcome).also { entered = it !== run && outcome.state == StepState.NEEDS_UNINSTALL } }
        }
        if (entered && after != null && after.origin == Origin.BACKGROUND) Notifications.needsReinstall(context, index, after.steps[index].step.displayName)
        return after
    }

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
        finishStep(context, index, outcome)
        if (startQueued(context)) return
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
        if (status != PackageInstaller.STATUS_PENDING_USER_ACTION) confirmIntents.remove(stepIndex)
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
        confirmIntents[index] = confirm
        if (AppVisibility.visible) {
            context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } else {
            Notifications.confirmInstall(context, index, name, confirm)
        }
    }

    /** Re-runs step [index] after a user action (confirm, uninstall): network CONNECTED only, even for a background run. */
    private fun retry(context: Context, index: Int) {
        confirmIntents.remove(index)
        // A signature uninstall succeeded: the app left for the reinstall, so its absence is never a removal.
        InstallRunStore.state.value?.steps?.getOrNull(index)?.takeIf { it.state == StepState.NEEDS_UNINSTALL }?.let { s ->
            prefs(context).updateSeenInstalled { it - seenKey(s.step.kind, s.step.refId) }
        }
        InstallRunStore.update(context) { cur -> cur?.let { InstallRun.retry(it, index) } }
        InstallWorker.enqueue(context, Origin.UI, replace = false)
    }

    private fun prefs(context: Context) = (context.applicationContext as RelumeApp).prefs

    private fun stepAt(index: Int, expected: StepState): StepRun {
        val run = checkNotNull(InstallRunStore.state.value) { "install run: no run, expected step $index in state $expected" }
        val s = run.steps.getOrNull(index) ?: throw IndexOutOfBoundsException("install run: step $index of ${run.steps.size}")
        check(s.state == expected) { "install run: step $index (${s.step.displayName}) is ${s.state}, expected $expected" }
        return s
    }

    /** A committed step whose session vanished: installed if the phone now has the target, else `session lost`. */
    private fun lostSession(context: Context, s: StepRun): StepOutcome {
        val d = checkNotNull(s.step.download) { "step ${s.step.displayName}: committed without a download" }
        val pkg = s.step.packageName
        val info = Inventory.packageInfo(context, pkg)
        val done = VerifyRules.installedBySession(d, info?.let { Inventory.packageFacts(it) }, info?.lastUpdateTime, s.priorUpdateTime)
        return if (done) StepOutcome(StepState.INSTALLED, null, null)
        else StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "session lost")
    }
}
