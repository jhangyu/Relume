package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.Assignment
import io.github.jhangyu.relume.api.DeviceKind
import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

class RemovedAppsTest {
    private val receipt = JsonObject(emptyMap())

    private fun device(vararg a: Assignment) = DeviceView(
        1, "Phone", DeviceKind.APP, null, null, null, null, "t", null, a.toList(), emptyList(),
    )

    private fun item(id: Int, installed: Boolean, onPhone: Boolean) =
        Assignment(id, pending = !onPhone, installed = receipt.takeIf { installed }, installedReported = receipt.takeIf { onPhone })

    @Test
    fun onlyAnAppInstalledBeforeAndNowGoneIsRemoved() {
        val d = device(item(1, installed = true, onPhone = false), item(2, installed = false, onPhone = false), item(3, installed = true, onPhone = true))
        val p = plan(step(StepKind.ITEM, 1), step(StepKind.ITEM, 2), step(StepKind.ITEM, 3, action = PlanAction.UP_TO_DATE))
        assertEquals(setOf(1), removedFromPhone(d, p, null).items)
    }

    @Test
    fun signatureReinstallInProgressOrUnfinishedIsKept() {
        val d = device(item(1, installed = true, onPhone = false))
        val p = plan(step(StepKind.ITEM, 1))
        val needs = InstallRun.finish(InstallRun.create(p, Origin.BACKGROUND, null), 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "x"))
        assertEquals(emptySet<Int>(), removedFromPhone(d, p, needs).items)
        val failed = InstallRun.finish(InstallRun.create(p, Origin.BACKGROUND, null), 0, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "x"))
        assertEquals(emptySet<Int>(), removedFromPhone(d, p, failed).items)
    }
}
