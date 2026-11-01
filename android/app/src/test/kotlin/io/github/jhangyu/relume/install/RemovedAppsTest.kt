package io.github.jhangyu.relume.install

import android.content.pm.PackageInstaller
import io.github.jhangyu.relume.api.Assignment
import io.github.jhangyu.relume.api.DeviceKind
import io.github.jhangyu.relume.api.DeviceProject
import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.StepKind
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The removal check right before an install step runs: seen installed while assigned, absent now -> skip and unassign. */
class RemovedAppsTest {
    private val mark = JsonObject(emptyMap())
    private val sessionLost = StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "session lost")

    private fun device(vararg a: Assignment, projects: List<DeviceProject> = emptyList()) = DeviceView(
        1, "Phone", DeviceKind.APP, null, null, null, null, "t", null, a.toList(), projects,
    )

    /** An old install receipt always exists; [onPhone] is the phone's report at that sync. */
    private fun item(id: Int, onPhone: Boolean) =
        Assignment(id, pending = !onPhone, installed = mark, installedReported = mark.takeIf { onPhone })

    /** Applies the syncs in order from a cleared set (sign-in) and returns the set after the last one. */
    private fun seenAfter(vararg syncs: DeviceView, lastRun: RunState? = null) =
        syncs.fold(emptySet<String>()) { seen, d -> seenInstalled(seen, d, lastRun) }

    private val requested = plan(step(StepKind.ITEM, 1), step(StepKind.ITEM, 2))

    /** Background run waiting on a confirmation (open session 7) for another app. */
    private val waiting = checkNotNull(
        InstallRun.move(InstallRun.create(plan(step(StepKind.ITEM, 5)), Origin.BACKGROUND, null), 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 7),
    )

    /** Item 1 was on the phone, then the user removed it; syncs ran while [run] was the last run. */
    private fun seenRemoved(run: RunState?) = seenAfter(device(item(1, onPhone = true)), device(item(1, onPhone = false)), lastRun = run)

    private fun assertSkippedAndUnassigned(run: RunState, seen: Set<String>) {
        assertTrue(removedFromPhone(run.steps[0], seen, onPhone = false))
        assertEquals(listOf(2) to emptyList<Int>(), assignmentsWithout(device(item(1, false), item(2, true)), run.steps[0].step))
        val skipped = InstallRun.finish(run, 0, removedOutcome)
        assertEquals(StepState.REMOVED, skipped.steps[0].state)
        assertFalse("the unassign tells the server, no receipt", skipped.steps[0].receiptDue)
        assertEquals("the next app still runs", 1, skipped.nextRunnable())
    }

    @Test
    fun aQueuedPlanDoesNotReinstallARemovedApp() {
        val queued = InstallRun.admit(waiting, requested, Origin.BACKGROUND, setOf(7), workPending = false) { sessionLost }.run
        val started = checkNotNull(InstallRun.dequeue(InstallRun.finish(queued, 0, InstallResult.from(PackageInstaller.STATUS_SUCCESS, null))))
        // the user removed the app while the plan waited, and a sync ran after the queued run started
        assertSkippedAndUnassigned(started, seenInstalled(seenRemoved(queued), device(item(1, onPhone = false)), started))
    }

    @Test
    fun aStaleRecordStartDoesNotReinstallARemovedApp() {
        val a = InstallRun.admit(waiting, requested, Origin.BACKGROUND, emptySet(), workPending = false) { sessionLost }
        assertTrue(a.started)
        assertSkippedAndUnassigned(a.run, seenRemoved(waiting))
    }

    @Test
    fun neverInstalledOrPresentAppsInstall() {
        val run = InstallRun.create(requested, Origin.BACKGROUND, null)
        // just assigned, never reported installed
        val pending = device(item(1, onPhone = false))
        assertFalse(removedFromPhone(run.steps[0], seenAfter(device(), pending, pending), onPhone = false))
        // on the phone: an upgrade
        assertFalse(removedFromPhone(run.steps[0], seenAfter(device(item(1, onPhone = true))), onPhone = true))
        // reassigned on the web while absent: tracking starts again
        assertFalse(removedFromPhone(run.steps[0], seenAfter(device(item(1, onPhone = true)), device(), pending), onPhone = false))
        // fresh sign-in
        assertFalse(removedFromPhone(run.steps[0], seenAfter(pending), onPhone = false))
    }

    @Test
    fun aSignatureReinstallIsNeverARemoval() {
        val p = plan(step(StepKind.ITEM, 1))
        val seen = seenAfter(device(item(1, onPhone = true)))
        val needs = InstallRun.finish(InstallRun.create(p, Origin.BACKGROUND, null), 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "x"))
        // removed outside the dialog while it waits for the uninstall: a sync drops it, the next run installs it
        val after = seenInstalled(seen, device(item(1, onPhone = false)), needs)
        assertFalse(removedFromPhone(InstallRun.create(p, Origin.BACKGROUND, needs).steps[0], after, onPhone = false))
    }

    @Test
    fun aUiRequestDropsEveryAppItInstallsFromTheSeenSet() {
        // the user removed project 3 earlier, then taps Install on item 4 that needs it and confirms the listed dependency
        val project = DeviceProject(3, assigned = true, pending = false, installedTag = "v3", installedReported = mark)
        val seen = seenAfter(device(item(4, onPhone = true), projects = listOf(project)))
        val ui = plan(step(StepKind.PROJECT, 3), step(StepKind.ITEM, 4, needs = listOf(3)))
        assertEquals(setOf(seenKey(StepKind.PROJECT, 3), seenKey(StepKind.ITEM, 4)), requestedKeys(ui))
        val run = InstallRun.create(ui, Origin.UI, null)
        assertFalse("the confirmed dependency installs", removedFromPhone(run.steps[0], seen - requestedKeys(ui), onPhone = false))
    }

    @Test
    fun aRemovedProjectIsUnassignedAndBlocksItsDependents() {
        val project = DeviceProject(3, assigned = true, pending = false, installedTag = "v3", installedReported = null)
        val run = InstallRun.create(plan(step(StepKind.PROJECT, 3), step(StepKind.ITEM, 4, needs = listOf(3))), Origin.BACKGROUND, null)
        assertEquals(listOf(4) to emptyList<Int>(), assignmentsWithout(device(item(4, true), projects = listOf(project)), run.steps[0].step))
        assertEquals(StepState.DEPENDENCY_FAILED, InstallRun.finish(run, 0, removedOutcome).steps[1].state)
    }
}
