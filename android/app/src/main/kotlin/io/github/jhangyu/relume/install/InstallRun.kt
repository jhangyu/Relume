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

enum class StartResult { Started, Busy, Corrupt }

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
    @SerialName("server_error") SERVER_ERROR;

    /** A step in one of these states blocks a new run (spec 9.1). */
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
)

@Serializable
data class RunState(
    val revision: String,
    val origin: Origin,
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
) {
    val isActive get() = steps.any { it.state.active }

    fun nextRunnable(): Int? = steps.indexOfFirst { it.state.runnable }.takeIf { it >= 0 }

    fun indexOfSession(sessionId: Int): Int? =
        steps.indexOfFirst { it.sessionId == sessionId && (it.state == StepState.COMMITTED || it.state == StepState.AWAITING_USER) }
            .takeIf { it >= 0 }
}

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
        return RunState(
            plan.revision, origin, steps, stalePlan = false, synced = false, syncError = null,
            outbox = unsent, outboxError = null,
        ).propagate()
    }

    /** Moves step [index] from one of [from] to [to]; null when the step is no longer in [from] (cancelled meanwhile). */
    fun move(run: RunState, index: Int, from: Set<StepState>, to: StepState, sessionId: Int? = run.steps[index].sessionId): RunState? {
        val s = run.steps[index]
        if (s.state !in from) return null
        return run.replace(index, s.copy(state = to, sessionId = sessionId))
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

    /** Removes [sent] from the outbox; [error] is a refusal other than a stale plan (null = accepted or stale). */
    fun outboxSent(run: RunState, sent: InstallReceipt, error: String?): RunState {
        val i = run.outbox.indexOf(sent)
        check(i >= 0) { "outbox of run ${run.revision}: receipt ${sent.kind} ${sent.refId} not found" }
        return run.copy(outbox = run.outbox.filterIndexed { j, _ -> j != i }, outboxError = error ?: run.outboxError)
    }

    /** Replaces the error text of step [index] without changing its state or receipt. */
    fun note(run: RunState, index: Int, text: String): RunState = run.replace(index, run.steps[index].copy(errorText = text))

    /** User cancel: every step that has not ended becomes `cancelled`; dependents become `dependency_failed`. */
    fun cancelAll(run: RunState): RunState =
        run.steps.indices.fold(run) { acc, i ->
            val st = acc.steps[i].state
            if (st.runnable || st.active) finish(acc, i, StepOutcome(StepState.CANCELLED, ErrorCodes.USER_REJECTED, "cancelled by the user")) else acc
        }

    /** Re-runs step [index] (after an uninstall or a lost confirm prompt) and the dependents it blocked. */
    fun retry(run: RunState, index: Int): RunState {
        val target = run.steps[index]
        val reopened = run.steps.mapIndexed { i, s ->
            val blocked = target.step.kind == StepKind.PROJECT && s.state == StepState.DEPENDENCY_FAILED &&
                target.step.refId in s.step.needs
            if (i == index || blocked) s.copy(state = StepState.PENDING, errorCode = null, errorText = null, sessionId = null, receiptDue = false, downloadedBytes = 0) else s
        }
        return run.copy(steps = reopened, synced = false, syncError = null).propagate()
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
            StepKind.COMPANION -> error("companion step ${s.step.refId} has no receipt (spec 5.1)")
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

    private val FAILING = setOf(
        StepState.FAILED, StepState.CANCELLED, StepState.SERVER_ERROR, StepState.NEEDS_UNINSTALL, StepState.DEPENDENCY_FAILED,
    )
    private val RECEIPTED = setOf(
        StepState.INSTALLED, StepState.FAILED, StepState.CANCELLED, StepState.NEEDS_UNINSTALL, StepState.DEPENDENCY_FAILED,
    )

    private fun receipted(step: PlanStep, state: StepState) =
        step.kind != StepKind.COMPANION && step.download != null && state in RECEIPTED

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
