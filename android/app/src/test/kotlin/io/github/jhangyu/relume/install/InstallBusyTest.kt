package io.github.jhangyu.relume.install

import android.content.pm.PackageInstaller
import io.github.jhangyu.relume.api.ReceiptResult
import io.github.jhangyu.relume.api.StepKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Whether a requested run waits is decided by Relume's open PackageInstaller sessions, not by the run record. */
class InstallBusyTest {
    private val sessionLost = StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "session lost")
    private val lost: (StepRun) -> StepOutcome = { sessionLost }
    private val requested = plan(step(StepKind.ITEM, 9))

    /** Old background run: step 0 in [state] with session [SESSION], step 1 not started. */
    private fun oldRun(state: StepState, session: Int? = SESSION): RunState {
        val run = InstallRun.create(plan(step(StepKind.ITEM, 1), step(StepKind.ITEM, 2)), Origin.BACKGROUND, null)
        return checkNotNull(InstallRun.move(run, 0, setOf(StepState.PENDING), state, session))
    }

    @Test
    fun aRecordWithoutASystemSessionNeverBlocks() {
        for (state in listOf(StepState.AWAITING_USER, StepState.COMMITTED)) {
            // no session at all, and an unrelated live session of Relume
            for (live in listOf(emptySet(), setOf(SESSION + 1))) {
                val a = InstallRun.admit(oldRun(state), requested, Origin.UI, live, lost)
                assertTrue("$state live=$live", a.started)
                assertNull(a.surface)
                assertEquals(listOf(9), a.run.steps.map { it.step.refId })
                assertEquals(Origin.UI, a.run.origin)
                // the old step was closed out through the reconcile outcome and its receipt goes with the new run
                val r = a.run.outbox.single()
                assertEquals(1 to ReceiptResult.FAILED, r.refId to r.result)
                assertEquals("session lost", r.errorText)
            }
        }
        // a download in progress holds no system session: it is cancelled, its receipt is sent
        val a = InstallRun.admit(oldRun(StepState.DOWNLOADING, session = null), requested, Origin.UI, emptySet(), lost)
        assertTrue(a.started)
        assertEquals(ErrorCodes.USER_REJECTED, a.run.outbox.single().errorCode)
    }

    @Test
    fun anOpenSessionQueuesTheRequestUntilItEnds() {
        val old = oldRun(StepState.AWAITING_USER)
        val a = InstallRun.admit(old, requested, Origin.UI, setOf(SESSION), lost)
        assertFalse(a.started)
        assertEquals(0, a.surface)
        assertEquals(old.steps, a.run.steps)
        assertEquals(QueuedStart(requested, Origin.UI, 0), a.run.queued)
        val queued = a.run
        assertNull("the session is still open", InstallRun.dequeue(queued))
        // a lost confirmation re-runs the step for a fresh prompt: the request keeps waiting for it
        assertNull(InstallRun.dequeue(InstallRun.retry(queued, 0)))

        val ends = mapOf(
            "success" to InstallResult.from(PackageInstaller.STATUS_SUCCESS, null),
            "failure" to InstallResult.from(PackageInstaller.STATUS_FAILURE, "x"),
            "user cancel or abandon (result broadcast)" to InstallResult.from(PackageInstaller.STATUS_FAILURE_ABORTED, null),
            "session gone (reconcile)" to sessionLost,
        )
        for ((name, outcome) in ends) {
            val next = InstallRun.dequeue(InstallRun.finish(queued, 0, outcome))
            assertNotNull(name, next)
            next!!
            assertEquals(name, listOf(9), next.steps.map { it.step.refId })
            assertEquals(Origin.UI, next.origin)
            assertNull(next.queued)
            assertEquals(name, listOf(1), next.outbox.map { it.refId })
        }
        // cancel in the install sheet ends the session too
        assertEquals(listOf(9), InstallRun.dequeue(InstallRun.cancelAll(queued))!!.steps.map { it.step.refId })

        // a committed session the system is still installing queues without a prompt
        val committed = InstallRun.admit(oldRun(StepState.COMMITTED), requested, Origin.BACKGROUND, setOf(SESSION), lost)
        assertFalse(committed.started)
        assertNull(committed.surface)
        assertEquals(QueuedStart(requested, Origin.BACKGROUND, 0), committed.run.queued)
    }

    @Test
    fun nothingPendingStartsAsToday() {
        assertEquals(Admission(InstallRun.create(requested, Origin.UI, null), true, null), InstallRun.admit(null, requested, Origin.UI, emptySet(), lost))
        val ended = InstallRun.finish(oldRun(StepState.AWAITING_USER), 0, InstallResult.from(PackageInstaller.STATUS_SUCCESS, null))
        val a = InstallRun.admit(ended, requested, Origin.UI, emptySet(), lost)
        assertEquals(Admission(InstallRun.create(requested, Origin.UI, ended), true, null), a)
    }

    private companion object {
        const val SESSION = 636061859
    }
}
