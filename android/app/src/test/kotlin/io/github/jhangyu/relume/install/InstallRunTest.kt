package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.ReceiptResult
import io.github.jhangyu.relume.api.StepKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class InstallRunTest {
    private fun states(run: RunState) = run.steps.map { it.state }
    private val failed = StepOutcome(StepState.FAILED, ErrorCodes.HASH_MISMATCH, "bad hash")

    @Test
    fun dependencyFailurePropagatesToDependentsOnly() {
        // project 5 and 6; item 5 shares ref id 5 with the project but is an item (needs holds project ids only)
        val run = InstallRun.create(plan(
            step(StepKind.PROJECT, 5), step(StepKind.PROJECT, 6), step(StepKind.ITEM, 5),
            step(StepKind.ITEM, 1, needs = listOf(5)), step(StepKind.ITEM, 2, needs = listOf(6)), step(StepKind.ITEM, 3, needs = listOf(5)),
        ), Origin.UI, null)
        val after = InstallRun.finish(run, 0, failed)
        assertEquals(listOf(StepState.FAILED, StepState.PENDING, StepState.PENDING, StepState.DEPENDENCY_FAILED,
            StepState.PENDING, StepState.DEPENDENCY_FAILED), states(after))
        assertEquals(ErrorCodes.DEPENDENCY_FAILED, after.steps[3].errorCode)
        assertTrue(after.steps[3].receiptDue)
        assertEquals(1, after.nextRunnable())

        // a failing item with the same ref id as a project blocks nothing
        val itemFailed = InstallRun.finish(run, 2, failed)
        assertEquals(listOf(StepState.PENDING, StepState.PENDING, StepState.FAILED, StepState.PENDING,
            StepState.PENDING, StepState.PENDING), states(itemFailed))

        // receipt of the failed project carries its tag and code
        val r = InstallRun.receipt(after.steps[0])
        assertEquals(ReceiptResult.FAILED, r.result)
        assertEquals("v5", r.tag)
        assertEquals(ErrorCodes.HASH_MISMATCH, r.errorCode)

        // retry reopens the project and its blocked dependents
        val retried = InstallRun.retry(InstallRun.finish(after, 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "x")), 0)
        assertEquals(listOf(StepState.PENDING, StepState.PENDING, StepState.PENDING, StepState.PENDING,
            StepState.PENDING, StepState.PENDING), states(retried))
    }

    @Test
    fun cancelBlocksDependents() {
        val run = InstallRun.create(plan(
            step(StepKind.PROJECT, 5), step(StepKind.ITEM, 1, needs = listOf(5)), step(StepKind.ITEM, 2),
        ), Origin.UI, null)
        val one = InstallRun.finish(run, 0, InstallResult.from(android.content.pm.PackageInstaller.STATUS_FAILURE_ABORTED, null))
        assertEquals(listOf(StepState.CANCELLED, StepState.DEPENDENCY_FAILED, StepState.PENDING), states(one))
        assertEquals(ErrorCodes.USER_REJECTED, one.steps[0].errorCode)

        val committed = InstallRun.move(run, 0, setOf(StepState.PENDING), StepState.COMMITTED, 77)!!
        assertTrue(committed.isActive)
        assertEquals(0, committed.indexOfSession(77))
        val all = InstallRun.cancelAll(committed)
        assertEquals(listOf(StepState.CANCELLED, StepState.DEPENDENCY_FAILED, StepState.CANCELLED), states(all))
        assertFalse(all.isActive)
        assertNull(all.nextRunnable())
        // a worker that still holds the old state cannot move a cancelled step on, nor overwrite it with a result
        assertNull(InstallRun.move(all, 2, setOf(StepState.PENDING, StepState.DOWNLOADING), StepState.DOWNLOADING))
        assertEquals(all, InstallRun.finish(all, 0, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "late")))

        // an uninstall result only applies to the same run, step and package in needs_uninstall
        val needs = InstallRun.finish(run, 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "x"))
        val pkg = needs.steps[0].step.packageName
        assertNull(InstallRun.staleUninstall(needs, "rev", 0, pkg))
        assertTrue(InstallRun.staleUninstall(null, "rev", 0, pkg)!!.contains("no install run"))
        assertTrue(InstallRun.staleUninstall(needs.copy(revision = "new"), "rev", 0, pkg)!!.contains("revision is new, expected rev"))
        assertTrue(InstallRun.staleUninstall(needs, "rev", 7, pkg)!!.contains("expected step 7"))
        assertTrue(InstallRun.staleUninstall(needs, "rev", 0, "other")!!.contains("expected other"))
        assertTrue(InstallRun.staleUninstall(run, "rev", 0, pkg)!!.contains("is PENDING, expected NEEDS_UNINSTALL"))
    }

    @Test
    fun serverErrorStepsAreSkipped() {
        val run = InstallRun.create(plan(
            step(StepKind.PROJECT, 5, action = PlanAction.ERROR), step(StepKind.ITEM, 1, needs = listOf(5)),
            step(StepKind.ITEM, 2, action = PlanAction.UP_TO_DATE), step(StepKind.ITEM, 3),
        ), Origin.BACKGROUND, null)
        assertEquals(listOf(StepState.SERVER_ERROR, StepState.DEPENDENCY_FAILED, StepState.UP_TO_DATE,
            StepState.PENDING), states(run))
        assertEquals("NOT_DOWNLOADED", run.steps[0].errorText)
        assertFalse(run.steps[0].receiptDue)
        assertEquals(listOf(5, 1, 2, 3), run.steps.map { it.step.refId })
        assertEquals(3, run.nextRunnable())

        val item = InstallRun.finish(run, 3, StepOutcome(StepState.INSTALLED, null, null))
        assertEquals(103, InstallRun.receipt(item.steps[3]).artifactId)
    }
}
