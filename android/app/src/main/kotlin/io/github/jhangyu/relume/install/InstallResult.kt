package io.github.jhangyu.relume.install

import android.content.pm.PackageInstaller

/** Maps a PackageInstaller status broadcast to a step outcome (spec 9.4). No automatic retry. */
object InstallResult {

    /** Constant names for error texts; the values are compile-time constants, so this runs on the JVM. */
    val STATUS_NAMES: Map<Int, String> = mapOf(
        PackageInstaller.STATUS_PENDING_USER_ACTION to "STATUS_PENDING_USER_ACTION",
        PackageInstaller.STATUS_SUCCESS to "STATUS_SUCCESS",
        PackageInstaller.STATUS_FAILURE to "STATUS_FAILURE",
        PackageInstaller.STATUS_FAILURE_BLOCKED to "STATUS_FAILURE_BLOCKED",
        PackageInstaller.STATUS_FAILURE_ABORTED to "STATUS_FAILURE_ABORTED",
        PackageInstaller.STATUS_FAILURE_INVALID to "STATUS_FAILURE_INVALID",
        PackageInstaller.STATUS_FAILURE_CONFLICT to "STATUS_FAILURE_CONFLICT",
        PackageInstaller.STATUS_FAILURE_STORAGE to "STATUS_FAILURE_STORAGE",
        PackageInstaller.STATUS_FAILURE_INCOMPATIBLE to "STATUS_FAILURE_INCOMPATIBLE",
        PackageInstaller.STATUS_FAILURE_TIMEOUT to "STATUS_FAILURE_TIMEOUT",
    )

    fun statusName(status: Int) = STATUS_NAMES[status] ?: "unknown PackageInstaller status $status"

    /** [message] is `EXTRA_STATUS_MESSAGE` (may be absent). */
    fun from(status: Int, message: String?): StepOutcome {
        val text = statusName(status) + (message?.let { ": $it" } ?: "")
        return when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> StepOutcome(StepState.AWAITING_USER, null, null)
            PackageInstaller.STATUS_SUCCESS -> StepOutcome(StepState.INSTALLED, null, null)
            PackageInstaller.STATUS_FAILURE_ABORTED -> StepOutcome(StepState.CANCELLED, ErrorCodes.USER_REJECTED, text)
            PackageInstaller.STATUS_FAILURE_CONFLICT -> StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, text)
            else -> StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, text)
        }
    }
}
