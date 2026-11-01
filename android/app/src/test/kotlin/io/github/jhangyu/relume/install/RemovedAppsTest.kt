package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.Assignment
import io.github.jhangyu.relume.api.DeviceKind
import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

/** Each case is a sequence of syncs (UI, install worker or check worker) ending with the check worker's decision. */
class RemovedAppsTest {
    private val mark = JsonObject(emptyMap())
    private val none = emptySet<Int>()

    private fun device(vararg a: Assignment) = DeviceView(
        1, "Phone", DeviceKind.APP, null, null, null, null, "t", null, a.toList(), emptyList(),
    )

    /** An old install receipt always exists; [onPhone] is the phone's report at that sync. */
    private fun item(id: Int, onPhone: Boolean) =
        Assignment(id, pending = !onPhone, installed = mark, installedReported = mark.takeIf { onPhone })

    /** Applies the syncs in order from a cleared set (sign-in) and returns the set after the last one. */
    private fun seenAfter(vararg syncs: DeviceView, lastRun: RunState? = null) =
        syncs.fold(emptySet<String>()) { seen, d -> seenInstalled(seen, d, lastRun) }

    @Test
    fun uninstallThenUiSyncThenCheckUnassigns() {
        val absent = device(item(1, onPhone = false))
        val seen = seenAfter(device(item(1, onPhone = true)), absent, absent)
        assertEquals(setOf(1), removedFromPhone(seen, absent, plan(step(StepKind.ITEM, 1))).items)
    }

    @Test
    fun reassignedOnTheWebWhileAbsentIsKept() {
        val absent = device(item(1, onPhone = false))
        val seen = seenAfter(device(item(1, onPhone = true)), device(), absent)
        assertEquals(none, removedFromPhone(seen, absent, plan(step(StepKind.ITEM, 1))).items)
    }

    @Test
    fun justAssignedInstallPendingIsKept() {
        val pending = device(item(1, onPhone = false))
        val seen = seenAfter(device(), pending, pending)
        assertEquals(none, removedFromPhone(seen, pending, plan(step(StepKind.ITEM, 1))).items)
    }

    @Test
    fun presentWithPendingUpdateIsKept() {
        val present = device(item(1, onPhone = true))
        val seen = seenAfter(present, present)
        assertEquals(none, removedFromPhone(seen, present, plan(step(StepKind.ITEM, 1, action = PlanAction.INSTALL))).items)
    }

    @Test
    fun signatureReinstallOrUnfinishedStepIsKept() {
        val p = plan(step(StepKind.ITEM, 1))
        val absent = device(item(1, onPhone = false))
        val needs = InstallRun.finish(InstallRun.create(p, Origin.BACKGROUND, null), 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "x"))
        val failed = InstallRun.finish(InstallRun.create(p, Origin.BACKGROUND, null), 0, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "x"))
        for (run in listOf(needs, failed)) {
            val seen = seenInstalled(seenAfter(device(item(1, onPhone = true))), absent, run)
            assertEquals(none, removedFromPhone(seen, absent, p).items)
            // A later run for another app replaces the last run: the app stays assigned until it is seen installed again.
            assertEquals(none, removedFromPhone(seenInstalled(seen, absent, null), absent, p).items)
        }
    }

    @Test
    fun freshSignInUnassignsNothing() {
        val absent = device(item(1, onPhone = false))
        assertEquals(none, removedFromPhone(seenAfter(absent), absent, plan(step(StepKind.ITEM, 1))).items)
    }
}
