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
    private val mark = JsonObject(emptyMap())

    private fun device(vararg a: Assignment) = DeviceView(
        1, "Phone", DeviceKind.APP, null, null, null, null, "t", null, a.toList(), emptyList(),
    )

    /** An old install receipt always exists; [onPhone] is the phone's report at that sync. */
    private fun item(id: Int, onPhone: Boolean) =
        Assignment(id, pending = !onPhone, installed = mark, installedReported = mark.takeIf { onPhone })

    @Test
    fun onlyAPresentToAbsentTransitionIsRemoved() {
        val before = device(item(1, onPhone = true), item(2, onPhone = false), item(3, onPhone = true), item(4, onPhone = false))
        val now = device(item(1, onPhone = false), item(2, onPhone = false), item(3, onPhone = true), item(4, onPhone = true))
        val p = plan(step(StepKind.ITEM, 1), step(StepKind.ITEM, 2), step(StepKind.ITEM, 3, action = PlanAction.UP_TO_DATE))
        assertEquals(setOf(1), removedFromPhone(before, now, p, null).items)
    }

    @Test
    fun firstSyncWithNoPreviousReportRemovesNothing() {
        val now = device(item(1, onPhone = false))
        assertEquals(emptySet<Int>(), removedFromPhone(device(), now, plan(step(StepKind.ITEM, 1)), null).items)
    }

    @Test
    fun signatureReinstallInProgressOrUnfinishedIsKept() {
        val before = device(item(1, onPhone = true))
        val now = device(item(1, onPhone = false))
        val p = plan(step(StepKind.ITEM, 1))
        val needs = InstallRun.finish(InstallRun.create(p, Origin.BACKGROUND, null), 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "x"))
        assertEquals(emptySet<Int>(), removedFromPhone(before, now, p, needs).items)
        val failed = InstallRun.finish(InstallRun.create(p, Origin.BACKGROUND, null), 0, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "x"))
        assertEquals(emptySet<Int>(), removedFromPhone(before, now, p, failed).items)
    }
}
