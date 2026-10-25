package io.github.jhangyu.relume.install

import android.content.pm.PackageInstaller
import org.junit.Assert.assertEquals
import org.junit.Test

class InstallResultTest {
    @Test
    fun mapsEveryStatus() {
        val installerError = StepState.FAILED to ErrorCodes.INSTALLER_ERROR
        val expected = mapOf(
            PackageInstaller.STATUS_PENDING_USER_ACTION to (StepState.AWAITING_USER to null),
            PackageInstaller.STATUS_SUCCESS to (StepState.INSTALLED to null),
            PackageInstaller.STATUS_FAILURE_ABORTED to (StepState.CANCELLED to ErrorCodes.USER_REJECTED),
            PackageInstaller.STATUS_FAILURE_CONFLICT to (StepState.NEEDS_UNINSTALL to ErrorCodes.SIGNATURE_MISMATCH),
            PackageInstaller.STATUS_FAILURE to installerError,
            PackageInstaller.STATUS_FAILURE_BLOCKED to installerError,
            PackageInstaller.STATUS_FAILURE_INVALID to installerError,
            PackageInstaller.STATUS_FAILURE_STORAGE to installerError,
            PackageInstaller.STATUS_FAILURE_INCOMPATIBLE to installerError,
            PackageInstaller.STATUS_FAILURE_TIMEOUT to installerError,
        )
        assertEquals(InstallResult.STATUS_NAMES.keys, expected.keys)
        for ((status, want) in expected) {
            val got = InstallResult.from(status, "msg")
            assertEquals("status $status", want, got.state to got.errorCode)
        }
        val blocked = InstallResult.from(PackageInstaller.STATUS_FAILURE_BLOCKED, "policy")
        assertEquals("STATUS_FAILURE_BLOCKED: policy", blocked.errorText)
        val unknown = InstallResult.from(99, null)
        assertEquals(StepState.FAILED to ErrorCodes.INSTALLER_ERROR, unknown.state to unknown.errorCode)
        assertEquals("unknown PackageInstaller status 99", unknown.errorText)
    }
}
