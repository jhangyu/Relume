package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.InstallReceipt
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.ReceiptKind
import io.github.jhangyu.relume.api.ReceiptResult
import io.github.jhangyu.relume.api.StepKind
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Who started a run or a worker pass: the UI (any network) or the background check (unmetered, battery not low). */
@Serializable
enum class Origin { UI, BACKGROUND }

enum class StartResult { Started, Queued, Corrupt }

/** Per-step state of an install run (spec 9.1). */
@Serializable
enum class StepState {
    @SerialName("pending") PENDING,
    @SerialName("downloading") DOWNLOADING,
    @SerialName("verifying") VERIFYING,
    @SerialName("committed") COMMITTED,
    @SerialName("awaiting_user") AWAITING_USER,
    @SerialName("needs_uninstall") NEEDS_UNINSTALL,
    @SerialName("installed") INSTALLED,
    @SerialName("up_to_date") UP_TO_DATE,
    @SerialName("failed") FAILED,
    @SerialName("cancelled") CANCELLED,
    @SerialName("dependency_failed") DEPENDENCY_FAILED,
    @SerialName("server_error") SERVER_ERROR,
    /** The user removed the app from the phone: the step was skipped and the app unassigned from this phone. */
    @SerialName("removed") REMOVED;

    /** A step in one of these states has not ended; it blocks a new run only while its session or worker is live ([InstallRun.admit]). */
    val active get() = this in ACTIVE

    /** The worker picks up steps in these states; downloading/verifying resume after a stopped worker. */
    val runnable get() = this == PENDING || this == DOWNLOADING || this == VERIFYING

    private companion object {
        val ACTIVE = setOf(DOWNLOADING, VERIFYING, COMMITTED, AWAITING_USER)
    }
}

/** Receipt error codes the app sends (server `devices.APP_ERROR_CODES`). */
object ErrorCodes {
    const val HASH_MISMATCH = "HASH_MISMATCH"
    const val SIGNER_MISMATCH_FILE = "SIGNER_MISMATCH_FILE"
    const val INSTALLER_ERROR = "INSTALLER_ERROR"
    const val USER_REJECTED = "USER_REJECTED"
    const val DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
    const val SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
    const val VERSION_DOWNGRADE = "VERSION_DOWNGRADE"
}

/** Terminal outcome of one step: new state plus receipt code and human-readable text. */
data class StepOutcome(val state: StepState, val errorCode: String?, val errorText: String?)

@Serializable
data class StepRun(
    val step: PlanStep,
    val state: StepState,
    val errorCode: String?,
    val errorText: String?,
    /** PackageInstaller session of a committed / awaiting_user step. */
    val sessionId: Int?,
    /** A receipt for the current terminal state has not been sent yet. */
    val receiptDue: Boolean,
    /** Last receipt failure other than a stale plan (shown in the UI). */
    val receiptError: String?,
    /** Bytes of the `.part` file, updated in [PROGRESS_STEPS] steps of the plan size (spec 9.2). */
    val downloadedBytes: Long,
    /** The app left the phone through this run's signature reinstall (uninstall, then re-run): its absence is no removal. */
    val uninstalled: Boolean = false,
    /** `lastUpdateTime` of the installed package when this step's session was created; null = not installed then. */
    val priorUpdateTime: Long? = null,
)

@Serializable
data class RunState(
    val revision: String,
    val origin: Origin,
    /** Who triggers the next worker pass (its constraints): [origin] until a user retry makes it [Origin.UI] (spec 9.1). */
    val passOrigin: Origin,
    val steps: List<StepRun>,
    /** A receipt got 409 `stale plan`: the UI shows the new plan, no automatic reinstall (spec 9.4). */
    val stalePlan: Boolean,
    /** The final `POST /api/me/sync` after the last step ran. */
    val synced: Boolean,
    val syncError: String?,
    /** Receipts of earlier runs not sent yet; a new run takes them over so no result is lost (spec 9.4). */
    val outbox: List<InstallReceipt>,
    /** Last refusal of an [outbox] receipt other than a stale plan (shown in the UI). */
    val outboxError: String?,
    /** A requested run that waits for step [QueuedStart.after] to end (its open system session or its pending worker download). */
    val queued: QueuedStart? = null,
    /**
     * Builds the user declined in a background run, by app ([seenKey]) to the declined download sha256; carried into
     * each new run like [outbox]. See [InstallRun.declined].
     */
    val declined: Map<String, String> = emptyMap(),
) {
    val isActive get() = steps.any { it.state.active }

    fun nextRunnable(): Int? = steps.indexOfFirst { it.state.runnable }.takeIf { it >= 0 }

    fun indexOfSession(sessionId: Int): Int? =
        steps.indexOfFirst { it.sessionId == sessionId && (it.state == StepState.COMMITTED || it.state == StepState.AWAITING_USER) }
            .takeIf { it >= 0 }
}

/** A requested [plan] that starts once step [after] of the current run has ended (session finished, or download/verify ended). */
@Serializable
data class QueuedStart(val plan: InstallPlan, val origin: Origin, val after: Int)

/** Result of [InstallRun.admit]: the run to save, whether the requested plan started, and the step whose confirmation to show. */
data class Admission(val run: RunState, val started: Boolean, val surface: Int?)

/** What a confirmation request does: show the kept system prompt, re-run the step for a fresh one, or leave it to the user. */
enum class ConfirmAction { SHOW, RERUN, WAIT }

/** Pure run transitions (spec 9.1). Every function returns a new [RunState]. */
object InstallRun {

    /** Number of progress updates per download: each one rewrites the run file. */
    const val PROGRESS_STEPS = 100

    /**
     * Steps in server order; server `error` steps become `server_error` and block their dependents. The unsent receipts
     * of [previous] (its outbox and its due step receipts) move into the new run's outbox.
     */
    fun create(plan: InstallPlan, origin: Origin, previous: RunState?): RunState {
        val steps = plan.steps.map { step ->
            when (step.action) {
                PlanAction.INSTALL -> StepRun(step, StepState.PENDING, null, null, null, receiptDue = false, receiptError = null, downloadedBytes = 0)
                PlanAction.UP_TO_DATE -> StepRun(step, StepState.UP_TO_DATE, null, null, null, receiptDue = false, receiptError = null, downloadedBytes = 0)
                PlanAction.ERROR -> StepRun(step, StepState.SERVER_ERROR, null, step.reason, null, receiptDue = false, receiptError = null, downloadedBytes = 0)
            }
        }
        val unsent = previous?.let { p -> p.outbox + p.steps.filter { it.receiptDue }.map(::receipt) }.orEmpty()
        // The user asks for these apps in the UI: their declined builds are forgotten.
        val asked = if (origin == Origin.UI) plan.steps.filter { it.action == PlanAction.INSTALL }.map { seenKey(it.kind, it.refId) } else emptyList()
        return RunState(
            plan.revision, origin, origin, steps, stalePlan = false, synced = false, syncError = null,
            outbox = unsent, outboxError = null, declined = declined(previous) - asked.toSet(),
        ).propagate()
    }

    /**
     * Start decision for [plan]: [live] holds the ids of Relume's PackageInstaller sessions still open in the system,
     * [workPending] says an install worker pass is enqueued or running. A step blocks when its session is open, or when
     * it is `downloading` / `verifying` and [workPending] (the worker carries it on). The plan is then queued behind it
     * (a newer request replaces an older queued one) and an `awaiting_user` step's confirmation is to be shown.
     * Otherwise the plan starts; the old run's active steps are stale records and are closed first: committed /
     * awaiting ones with [lost] (the reconcile outcome), downloading / verifying ones `failed`.
     */
    fun admit(cur: RunState?, plan: InstallPlan, origin: Origin, live: Set<Int>, workPending: Boolean, lost: (StepRun) -> StepOutcome): Admission {
        val blocking = cur?.steps?.indexOfFirst { it.state.active && (it.sessionId in live || workPending && it.state in WORKER_HELD) } ?: -1
        if (cur != null && blocking >= 0) {
            val surface = blocking.takeIf { cur.steps[it].state == StepState.AWAITING_USER }
            return Admission(cur.copy(queued = QueuedStart(plan, origin, blocking)), started = false, surface = surface)
        }
        return Admission(create(plan, origin, cur?.let { closeStale(it, live, workPending, lost) }), started = true, surface = null)
    }

    /**
     * Ends the active steps the system no longer carries (stale records): committed / awaiting ones whose session is
     * not in [live] with [lost] (the reconcile outcome), downloading / verifying ones without [workPending] `failed`.
     */
    fun closeStale(run: RunState, live: Set<Int>, workPending: Boolean, lost: (StepRun) -> StepOutcome): RunState =
        run.steps.indices.fold(run) { acc, i ->
            val s = acc.steps[i]
            when {
                !s.state.active || s.sessionId in live -> acc
                s.state == StepState.COMMITTED || s.state == StepState.AWAITING_USER -> finish(acc, i, lost(s))
                workPending -> acc
                else -> finish(acc, i, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "install worker no longer runs"))
            }
        }

    /** The queued plan as the new run once the step it waits for has ended (its session finished); else null. */
    fun dequeue(run: RunState): RunState? {
        val q = run.queued ?: return null
        val s = run.steps[q.after]
        if (s.state.runnable || s.state.active) return null
        return create(q.plan, q.origin, run)
    }

    /**
     * Builds the user declined in a background run, per app ([seenKey]) to the download sha256: [run]'s carried record
     * plus its steps the user cancelled (confirmation or sheet cancel, `USER_REJECTED`) or that wait for a signature
     * uninstall the user has not accepted (`needs_uninstall`; a cancelled uninstall ends `cancelled`, `USER_REJECTED`).
     * The background check does not plan that same build again ([io.github.jhangyu.relume.sync.BackgroundStart]); a
     * newer build (another sha256) is planned, and a UI request for the app clears it ([create]). Failures that are not
     * the user's decline (download, verify, install errors) are not recorded and are retried at the next check.
     */
    fun declined(run: RunState?): Map<String, String> {
        if (run == null) return emptyMap()
        val now = if (run.origin != Origin.BACKGROUND) emptyList() else run.steps.mapNotNull { s ->
            val d = s.step.download
            val userDeclined = s.state == StepState.NEEDS_UNINSTALL ||
                s.state == StepState.CANCELLED && s.errorCode == ErrorCodes.USER_REJECTED
            if (d != null && userDeclined) seenKey(s.step.kind, s.step.refId) to d.sha256 else null
        }
        return run.declined + now
    }

    /** [kept]: the step's system prompt intent is held in this process; [byUser]: the user tapped Confirm. */
    fun confirmAction(kept: Boolean, byUser: Boolean): ConfirmAction = when {
        kept -> ConfirmAction.SHOW
        // Re-running abandons the session; it may be installing already (the user confirmed), so only the user decides.
        byUser -> ConfirmAction.RERUN
        else -> ConfirmAction.WAIT
    }

    /** Moves step [index] from one of [from] to [to]; null when the step is no longer in [from] (cancelled meanwhile). */
    fun move(
        run: RunState, index: Int, from: Set<StepState>, to: StepState, sessionId: Int? = run.steps[index].sessionId,
        priorUpdateTime: Long? = run.steps[index].priorUpdateTime,
    ): RunState? {
        val s = run.steps[index]
        if (s.state !in from) return null
        return run.replace(index, s.copy(state = to, sessionId = sessionId, priorUpdateTime = priorUpdateTime))
    }

    /** Ends step [index] with [outcome] and marks the dependents of a failed project step `dependency_failed`. */
    fun finish(run: RunState, index: Int, outcome: StepOutcome): RunState {
        val s = run.steps[index]
        // An ended step (e.g. cancelled) keeps its state: a late worker or session result cannot overwrite it.
        if (!s.state.runnable && !s.state.active) return run
        val done = s.copy(
            state = outcome.state,
            errorCode = outcome.errorCode,
            errorText = outcome.errorText,
            receiptDue = receipted(s.step, outcome.state),
            receiptError = null,
        )
        return run.replace(index, done).propagate()
    }

    /**
     * Records [bytes] of step [index]'s download when it crosses the next of [PROGRESS_STEPS] steps of the plan size;
     * otherwise returns [run] unchanged (no file write).
     */
    fun progress(run: RunState, index: Int, bytes: Long): RunState {
        val s = run.steps[index]
        val size = checkNotNull(s.step.download) { "progress of ${s.step.displayName}: step has no download" }.size
        if (s.state != StepState.DOWNLOADING || size <= 0) return run
        val stride = maxOf(1L, size / PROGRESS_STEPS)
        if (bytes != size && bytes / stride == s.downloadedBytes / stride) return run
        return run.replace(index, s.copy(downloadedBytes = bytes))
    }

    /**
     * Removes [sent] from the outbox; [error] is a refusal other than a stale plan (null = accepted or stale). [run] may
     * be a newer run than the one [sent] was read from: it took the outbox over, or a reset dropped it (then unchanged).
     */
    fun outboxSent(run: RunState, sent: InstallReceipt, error: String?): RunState {
        val i = run.outbox.indexOf(sent)
        if (i < 0) return run
        return run.copy(outbox = run.outbox.filterIndexed { j, _ -> j != i }, outboxError = error ?: run.outboxError)
    }

    /**
     * Records the answer to [sent], the receipt of step [index]: [stale] = 409 `stale plan`, [error] = another refusal.
     * Applied only while step [index] is still due with this receipt; a run started meanwhile (same revision possible,
     * it is a plan hash) is left unchanged, its outbox holds the copy of [sent].
     */
    fun receiptSent(run: RunState, index: Int, sent: InstallReceipt, stale: Boolean, error: String?): RunState {
        val s = run.steps.getOrNull(index)
        if (s == null || !s.receiptDue || receipt(s) != sent) return run
        return run.replace(index, s.copy(receiptDue = false, receiptError = if (stale) null else error)).copy(stalePlan = run.stalePlan || stale)
    }

    /**
     * The system uninstall of `needs_uninstall` step [index] did not succeed: the step ends in [outcome] (`cancelled`
     * when the user declined, else `failed`). Its failure receipt was already due as `needs_uninstall`; none is added.
     */
    fun endUninstall(run: RunState, index: Int, outcome: StepOutcome): RunState {
        val s = run.steps[index]
        check(s.state == StepState.NEEDS_UNINSTALL) { "end uninstall: step $index (${s.step.displayName}) is ${s.state}, expected NEEDS_UNINSTALL" }
        return run.replace(index, s.copy(state = outcome.state, errorCode = outcome.errorCode, errorText = outcome.errorText))
    }

    /** User cancel: every step that has not ended becomes `cancelled`; dependents become `dependency_failed`. */
    fun cancelAll(run: RunState): RunState =
        run.steps.indices.fold(run) { acc, i ->
            val st = acc.steps[i].state
            if (st.runnable || st.active) finish(acc, i, StepOutcome(StepState.CANCELLED, ErrorCodes.USER_REJECTED, "cancelled by the user")) else acc
        }

    /**
     * User re-run of step [index] (after an uninstall or a lost confirm prompt) and the dependents it blocked. Their due
     * receipts move to the outbox, so the server gets the first result and the re-run's; later passes need only a network.
     */
    fun retry(run: RunState, index: Int): RunState {
        val target = run.steps[index]
        val reopen = run.steps.indices.filter { i ->
            val s = run.steps[i]
            i == index || target.step.kind == StepKind.PROJECT && s.state == StepState.DEPENDENCY_FAILED && target.step.refId in s.step.needs
        }.toSet()
        val unsent = reopen.map { run.steps[it] }.filter { it.receiptDue }.map(::receipt)
        val steps = run.steps.mapIndexed { i, s ->
            if (i in reopen) s.copy(
                state = StepState.PENDING, errorCode = null, errorText = null, sessionId = null, receiptDue = false, downloadedBytes = 0,
                uninstalled = s.uninstalled || i == index && s.state == StepState.NEEDS_UNINSTALL,
            ) else s
        }
        return run.copy(passOrigin = Origin.UI, steps = steps, outbox = run.outbox + unsent, synced = false, syncError = null).propagate()
    }

    /**
     * Why an uninstall result for ([revision], [index], [packageName]) does not belong to [run] (null = it does):
     * the run can change while the system uninstall dialog is open.
     */
    fun staleUninstall(run: RunState?, revision: String, index: Int, packageName: String): String? {
        if (run == null) return "no install run, expected revision $revision"
        if (run.revision != revision) return "run revision is ${run.revision}, expected $revision"
        val s = run.steps.getOrNull(index) ?: return "run has ${run.steps.size} steps, expected step $index"
        if (s.step.packageName != packageName) return "step $index is ${s.step.packageName}, expected $packageName"
        if (s.state != StepState.NEEDS_UNINSTALL) return "step $index ($packageName) is ${s.state}, expected NEEDS_UNINSTALL"
        return null
    }

    /** Receipt for a step whose [StepRun.receiptDue] is set. */
    fun receipt(s: StepRun): InstallReceipt {
        val d = checkNotNull(s.step.download) { "receipt for ${s.step.kind} ${s.step.refId}: step has no download" }
        val kind = when (s.step.kind) {
            StepKind.ITEM -> ReceiptKind.ITEM
            StepKind.PROJECT -> ReceiptKind.PROJECT
        }
        val ok = s.state == StepState.INSTALLED
        return InstallReceipt(
            kind = kind,
            refId = s.step.refId,
            result = if (ok) ReceiptResult.INSTALLED else ReceiptResult.FAILED,
            artifactId = if (kind == ReceiptKind.ITEM) d.artifactId else null,
            tag = if (kind == ReceiptKind.PROJECT) d.tag else null,
            errorCode = if (ok) null else s.errorCode,
            errorText = if (ok) null else s.errorText,
        )
    }

    private val WORKER_HELD = setOf(StepState.DOWNLOADING, StepState.VERIFYING)
    private val FAILING = setOf(
        StepState.FAILED, StepState.CANCELLED, StepState.SERVER_ERROR, StepState.NEEDS_UNINSTALL, StepState.DEPENDENCY_FAILED,
        StepState.REMOVED,
    )
    private val RECEIPTED = setOf(
        StepState.INSTALLED, StepState.FAILED, StepState.CANCELLED, StepState.NEEDS_UNINSTALL, StepState.DEPENDENCY_FAILED,
    )

    private fun receipted(step: PlanStep, state: StepState) =
        step.download != null && state in RECEIPTED

    private fun RunState.replace(index: Int, s: StepRun) = copy(steps = steps.toMutableList().also { it[index] = s })

    /** `needs` holds project ids, so only project steps block; later not-started steps are the dependents (server order). */
    private fun RunState.propagate(): RunState {
        val failed = steps.filter { it.step.kind == StepKind.PROJECT && it.state in FAILING }.map { it.step.refId }.toSet()
        if (failed.isEmpty()) return this
        val out = steps.toMutableList()
        steps.forEachIndexed { i, s ->
            val cause = s.step.needs.firstOrNull { it in failed }
            if (cause != null && s.state == StepState.PENDING) {
                out[i] = s.copy(
                    state = StepState.DEPENDENCY_FAILED,
                    errorCode = ErrorCodes.DEPENDENCY_FAILED,
                    errorText = "dependency project $cause did not install",
                    receiptDue = receipted(s.step, StepState.DEPENDENCY_FAILED),
                )
            }
        }
        val next = copy(steps = out)
        return if (next == this) this else next.propagate()
    }
}
