package io.github.jhangyu.relume.install

import android.content.pm.PackageInstaller
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.ReceiptResult
import io.github.jhangyu.relume.api.StepKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import io.github.jhangyu.relume.sync.BackgroundStart
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
                val a = InstallRun.admit(oldRun(state), requested, Origin.UI, live, workPending = false, lost)
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
        // a download left by a dead worker (no install work pending) is a stale record: closed, its receipt is sent
        for (state in listOf(StepState.DOWNLOADING, StepState.VERIFYING)) {
            val a = InstallRun.admit(oldRun(state, session = null), requested, Origin.UI, emptySet(), workPending = false, lost)
            assertTrue("$state", a.started)
            val r = a.run.outbox.single()
            assertEquals(ErrorCodes.INSTALLER_ERROR to "install worker no longer runs", r.errorCode to r.errorText)
        }
    }

    @Test
    fun aDownloadInProgressQueuesTheRequestAndIsNotCancelled() {
        for (state in listOf(StepState.DOWNLOADING, StepState.VERIFYING)) {
            val old = oldRun(state, session = null)
            val a = InstallRun.admit(old, requested, Origin.BACKGROUND, emptySet(), workPending = true, lost)
            assertFalse("$state", a.started)
            assertNull(a.surface)
            assertEquals("$state: the download is untouched", old.steps, a.run.steps)
            assertEquals(QueuedStart(requested, Origin.BACKGROUND, 0), a.run.queued)
            // the step goes on to its session, still waited for; when it ends the queued run starts
            val committed = checkNotNull(InstallRun.move(a.run, 0, setOf(state), StepState.COMMITTED, SESSION))
            assertNull(InstallRun.dequeue(committed))
            for (outcome in listOf(InstallResult.from(PackageInstaller.STATUS_SUCCESS, null), StepOutcome(StepState.FAILED, ErrorCodes.HASH_MISMATCH, "bad"))) {
                val next = InstallRun.dequeue(InstallRun.finish(committed, 0, outcome))
                assertEquals("$state ${outcome.state}", listOf(9), next?.steps?.map { it.step.refId })
                assertEquals(Origin.BACKGROUND, next?.origin)
            }
        }
    }

    @Test
    fun anOpenSessionQueuesTheRequestUntilItEnds() {
        val old = oldRun(StepState.AWAITING_USER)
        val a = InstallRun.admit(old, requested, Origin.UI, setOf(SESSION), workPending = false, lost)
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
        val committed = InstallRun.admit(oldRun(StepState.COMMITTED), requested, Origin.BACKGROUND, setOf(SESSION), workPending = false, lost)
        assertFalse(committed.started)
        assertNull(committed.surface)
        assertEquals(QueuedStart(requested, Origin.BACKGROUND, 0), committed.run.queued)
    }

    @Test
    fun nothingPendingStartsAsToday() {
        assertEquals(Admission(InstallRun.create(requested, Origin.UI, null), true, null), InstallRun.admit(null, requested, Origin.UI, emptySet(), workPending = false, lost))
        val ended = InstallRun.finish(oldRun(StepState.AWAITING_USER), 0, InstallResult.from(PackageInstaller.STATUS_SUCCESS, null))
        val a = InstallRun.admit(ended, requested, Origin.UI, emptySet(), workPending = false, lost)
        assertEquals(Admission(InstallRun.create(requested, Origin.UI, ended), true, null), a)
    }

    @Test
    fun aNewStartNeverAbandonsASession() {
        // a kept prompt (foreground or background) is shown again; without it only the user's Confirm re-runs the step
        assertEquals(ConfirmAction.SHOW, InstallRun.confirmAction(kept = true, byUser = false))
        assertEquals(ConfirmAction.SHOW, InstallRun.confirmAction(kept = true, byUser = true))
        assertEquals(ConfirmAction.WAIT, InstallRun.confirmAction(kept = false, byUser = false))
        assertEquals(ConfirmAction.RERUN, InstallRun.confirmAction(kept = false, byUser = true))
    }

    @Test
    fun aQueuedBackgroundRevisionIsHandledOnlyWhenItsRunStarts() {
        val background = InstallPlan("night", requested.steps)
        assertEquals("old", BackgroundStart.recordedRevision(StartResult.Queued, background, "old"))
        assertEquals("old", BackgroundStart.recordedRevision(StartResult.Corrupt, background, "old"))
        assertEquals("night", BackgroundStart.recordedRevision(StartResult.Started, background, "old"))
        val queued = InstallRun.admit(oldRun(StepState.AWAITING_USER), background, Origin.BACKGROUND, setOf(SESSION), workPending = false, lost).run
        // a UI request replaces it: the next check plans the background revision again
        val replaced = InstallRun.admit(queued, requested, Origin.UI, setOf(SESSION), workPending = false, lost).run
        assertEquals(Origin.UI, replaced.queued?.origin)
        assertTrue(BackgroundStart.shouldStart(background, BackgroundStart.recordedRevision(StartResult.Queued, background, "old")))
        // when the queued background run starts, its revision is the one to record
        val started = checkNotNull(InstallRun.dequeue(InstallRun.finish(queued, 0, InstallResult.from(PackageInstaller.STATUS_SUCCESS, null))))
        assertEquals(Origin.BACKGROUND to "night", started.origin to started.revision)
    }

    private companion object {
        const val SESSION = 636061859
    }
}
