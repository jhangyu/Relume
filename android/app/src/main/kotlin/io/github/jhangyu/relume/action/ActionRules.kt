package io.github.jhangyu.relume.action

import android.content.pm.PackageInstaller
import io.github.jhangyu.relume.api.ActionKind
import io.github.jhangyu.relume.api.ActionOutcome
import io.github.jhangyu.relume.api.ActionRef
import io.github.jhangyu.relume.api.ActionResult
import io.github.jhangyu.relume.api.ActionSource
import io.github.jhangyu.relume.api.DeviceAction
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.install.InstallResult

/** How the companion carries out a delivered action (plan §1.6). */
sealed interface Route {
    data class Build(val ref: ActionRef) : Route
    data object PlayDirect : Route
    data object PlayPage : Route
    data object Remove : Route
    /** A kind/source/ref combination the server never sends: reported `failed`, not run. */
    data class Invalid(val reason: String) : Route
}

/** Pure decisions of the action executor (server `appmanage_store.ERROR_CODES`). Runs on the JVM. */
object ActionRules {
    const val INSTALLER_ERROR = "INSTALLER_ERROR"
    const val CANCELLED_BY_USER = "CANCELLED_BY_USER"
    const val UNINSTALL_FAILED = "UNINSTALL_FAILED"
    const val NOT_INSTALLED = "NOT_INSTALLED"
    const val PLAY_LOOKUP_FAILED = "PLAY_LOOKUP_FAILED"
    const val PLAY_DOWNLOAD_FAILED = "PLAY_DOWNLOAD_FAILED"
    const val ACTIVITY_START_FAILED = "ACTIVITY_START_FAILED"
    const val SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
    const val INSUFFICIENT_STORAGE = "INSUFFICIENT_STORAGE"

    private const val HTTP_NOT_FOUND = 404
    private const val HTTP_CONFLICT = 409
    private const val HTTP_BAD_GATEWAY = 502

    val DONE = ActionResult(ActionOutcome.DONE)

    fun failed(code: String, text: String) = ActionResult(ActionOutcome.FAILED, code, text)

    fun route(a: DeviceAction): Route {
        val what = "action ${a.id} (${a.action} of ${a.packageName}, source ${a.source}, ref ${a.ref})"
        return when (a.action) {
            ActionKind.REMOVE ->
                if (a.source == null && a.ref == null) Route.Remove else Route.Invalid("$what: remove expects no source and no ref")
            ActionKind.INSTALL, ActionKind.UPGRADE -> when (a.source) {
                null -> Route.Invalid("$what: install and upgrade expect a source, got none")
                ActionSource.BUILD -> a.ref?.let { Route.Build(it) } ?: Route.Invalid("$what: source build expects a ref, got none")
                ActionSource.PLAY_DIRECT -> if (a.ref == null) Route.PlayDirect else Route.Invalid("$what: source play_direct expects no ref")
                ActionSource.PLAY_PAGE -> if (a.ref == null) Route.PlayPage else Route.Invalid("$what: source play_page expects no ref")
            }
        }
    }

    /**
     * Result of a PackageInstaller install session ([remove] false, `play_direct`) or uninstall ([remove] true) status
     * broadcast; null for `STATUS_PENDING_USER_ACTION` (the confirmation is shown, the final status follows).
     */
    fun result(remove: Boolean, status: Int, message: String?): ActionResult? {
        val text = InstallResult.statusName(status) + (message?.let { ": $it" } ?: "")
        return when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> null
            PackageInstaller.STATUS_SUCCESS -> DONE
            PackageInstaller.STATUS_FAILURE_ABORTED -> ActionResult(ActionOutcome.CANCELLED, CANCELLED_BY_USER, text)
            else -> when {
                remove -> failed(UNINSTALL_FAILED, text)
                status == PackageInstaller.STATUS_FAILURE_CONFLICT -> failed(SIGNATURE_MISMATCH, text)
                status == PackageInstaller.STATUS_FAILURE_STORAGE -> failed(INSUFFICIENT_STORAGE, text)
                else -> failed(INSTALLER_ERROR, text)
            }
        }
    }

    /**
     * The server refused the action start with [status]: 404 (not this device's), 409 (no longer delivered: cancelled or
     * replaced on the web), 502 (the server already recorded `PLAY_LOOKUP_FAILED`). Nothing to report; the phone drops it.
     */
    fun closedByServer(status: Int): Boolean = status == HTTP_NOT_FOUND || status == HTTP_CONFLICT || status == HTTP_BAD_GATEWAY

    /**
     * A build action's step in its scoped plan: an install step is finished by its install receipt (null, nothing to
     * report); up_to_date is done; an error step or no step for the ref fails.
     */
    fun buildOutcome(ref: ActionRef, step: PlanStep?): ActionResult? = when (step?.action) {
        PlanAction.INSTALL -> null
        PlanAction.UP_TO_DATE -> DONE
        PlanAction.ERROR -> failed(INSTALLER_ERROR, "plan step ${ref.kind} ${ref.id}: ${step.reason}")
        null -> failed(INSTALLER_ERROR, "scoped plan for ${ref.kind} ${ref.id} has no step for it")
    }

    /**
     * A committed `play_direct` session is gone without a status broadcast (process death, session cleanup): done when
     * the phone has the version the server resolved, else failed.
     */
    fun sessionGone(installedVersionCode: Long?, targetVersionCode: Long?): ActionResult =
        if (installedVersionCode != null && installedVersionCode == targetVersionCode) DONE
        else failed(INSTALLER_ERROR, "install session ended without a result: installed version code $installedVersionCode, expected $targetVersionCode")
}
