package io.github.jhangyu.relume.install

import androidx.work.NetworkType
import io.github.jhangyu.relume.api.ReceiptResult
import io.github.jhangyu.relume.api.StepKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class InstallPersistenceTest {
    @get:Rule val tmp = TemporaryFolder()

    private val installed = StepOutcome(StepState.INSTALLED, null, null)

    @Test
    fun uiPassNeedsOnlyANetwork() {
        val ui = InstallWorker.constraints(Origin.UI)
        assertEquals(NetworkType.CONNECTED, ui.requiredNetworkType)
        assertFalse(ui.requiresBatteryNotLow())
        val background = InstallWorker.constraints(Origin.BACKGROUND)
        assertEquals(NetworkType.UNMETERED, background.requiredNetworkType)
        assertTrue(background.requiresBatteryNotLow())
    }

    @Test
    fun unsentReceiptsSurviveANewRunAndTheFile() {
        val first = InstallRun.create(plan(step(StepKind.ITEM, 1), step(StepKind.PROJECT, 2), step(StepKind.ITEM, 3)), Origin.BACKGROUND, null)
        val ended = InstallRun.finish(InstallRun.finish(first, 0, installed), 1, StepOutcome(StepState.FAILED, ErrorCodes.HASH_MISMATCH, "bad"))
        // step 3 was not run: no receipt due, nothing to carry
        val second = InstallRun.create(plan(step(StepKind.ITEM, 4)), Origin.UI, ended)
        assertEquals(listOf(1 to ReceiptResult.INSTALLED, 2 to ReceiptResult.FAILED), second.outbox.map { it.refId to it.result })
        assertEquals("v2", second.outbox[1].tag)

        // a third run before the outbox was flushed keeps it, plus the second run's due receipts
        val third = InstallRun.create(plan(step(StepKind.ITEM, 5)), Origin.UI, InstallRun.finish(second, 0, installed))
        assertEquals(listOf(1, 2, 4), third.outbox.map { it.refId })

        // process death: the outbox is in the run file
        val file = RunFile(File(tmp.root, "run.json"))
        file.write(third)
        val loaded = file.read()!!
        assertEquals(third, loaded)

        // flush: accepted / stale receipts leave the outbox, a refusal is kept for the UI
        val flushed = listOf(null, "ITEM 2: HTTP 400", null).zip(loaded.outbox).fold(loaded) { run, (error, r) -> InstallRun.outboxSent(run, r, error) }
        assertTrue(flushed.outbox.isEmpty())
        assertEquals("ITEM 2: HTTP 400", flushed.outboxError)
    }

    @Test
    fun aRetriedStepKeepsItsUnsentReceiptUntilFlushed() {
        val run = InstallRun.create(plan(step(StepKind.PROJECT, 1), step(StepKind.ITEM, 2, needs = listOf(1))), Origin.BACKGROUND, null)
        val uninstall = InstallRun.finish(run, 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "signer"))
        // neither the project's nor its dependent's receipt was sent before the user retried
        val retried = InstallRun.retry(uninstall, 0)
        assertEquals(listOf(1 to ErrorCodes.SIGNATURE_MISMATCH, 2 to ErrorCodes.DEPENDENCY_FAILED), retried.outbox.map { it.refId to it.errorCode })
        // the re-run's own result is due as well: the server gets both
        val rerun = InstallRun.finish(retried, 0, installed)
        assertTrue(rerun.steps[0].receiptDue)
        val flushed = rerun.outbox.fold(rerun) { acc, r -> InstallRun.outboxSent(acc, r, null) }
        assertTrue(flushed.outbox.isEmpty())
    }

    @Test
    fun aReceiptAnswerOnlyUpdatesTheRunItCameFrom() {
        val old = InstallRun.finish(
            InstallRun.create(plan(step(StepKind.ITEM, 1), step(StepKind.ITEM, 2)), Origin.UI, null), 1,
            StepOutcome(StepState.FAILED, ErrorCodes.HASH_MISMATCH, "bad"),
        )
        val sent = InstallRun.receipt(old.steps[1])
        // a new run started while the receipt was on its way: same plan (same revision) or a shorter one
        for (newer in listOf(InstallRun.create(plan(step(StepKind.ITEM, 1), step(StepKind.ITEM, 2)), Origin.UI, old), InstallRun.create(plan(step(StepKind.ITEM, 3)), Origin.UI, old))) {
            assertSame(newer, InstallRun.receiptSent(newer, 1, sent, stale = true, error = null))
            // its outbox holds the copy: sent once, removed once
            assertTrue(InstallRun.outboxSent(newer, sent, null).outbox.isEmpty())
        }
        val answered = InstallRun.receiptSent(old, 1, sent, stale = true, error = null)
        assertFalse(answered.steps[1].receiptDue)
        assertTrue(answered.stalePlan)
    }

    @Test
    fun passesContinuingAUiRetryNeedOnlyANetwork() {
        val run = InstallRun.create(plan(step(StepKind.ITEM, 1), step(StepKind.ITEM, 2)), Origin.BACKGROUND, null)
        assertEquals(Origin.BACKGROUND, run.passOrigin)
        val retried = InstallRun.retry(InstallRun.finish(run, 0, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "signer")), 0)
        // the session result of the re-run enqueues the next pass with passOrigin; notifications still follow origin
        val next = InstallRun.finish(retried, 0, installed)
        assertEquals(Origin.BACKGROUND, next.origin)
        assertEquals(NetworkType.CONNECTED, InstallWorker.constraints(next.passOrigin).requiredNetworkType)
        assertFalse(InstallWorker.constraints(next.passOrigin).requiresBatteryNotLow())
    }

    @Test
    fun corruptRunFileIsReportedAndResetClearsIt() {
        val path = File(tmp.root, "install-run.json")
        val file = RunFile(path)
        assertNull(file.read())
        for (bad in listOf("{not json", """{"revision":"r"}""")) {
            path.writeText(bad)
            try {
                file.read()
                fail("corrupt content read as a run: $bad")
            } catch (e: CorruptRunFile) {
                assertEquals(path.path, e.path)
                assertTrue(e.message!!.contains("install run file ${path.path}: expected a saved install run"))
            }
        }
        file.delete()
        assertNull(file.read())
    }

    @Test
    fun progressIsRecordedInPlanSizeSteps() {
        val size = 1000L
        val base = InstallRun.create(plan(step(StepKind.ITEM, 1)), Origin.UI, null)
        val s = base.steps[0]
        val big = base.copy(steps = listOf(s.copy(step = s.step.copy(download = s.step.download!!.copy(size = size)))))
        // not downloading yet: nothing recorded
        assertSame(big, InstallRun.progress(big, 0, 500))
        val run = InstallRun.move(big, 0, setOf(StepState.PENDING), StepState.DOWNLOADING)!!
        val ten = InstallRun.progress(run, 0, 10)
        assertEquals(10, ten.steps[0].downloadedBytes)
        // within the same 1 % step: unchanged object, no file write
        assertSame(ten, InstallRun.progress(ten, 0, 19))
        assertEquals(size, InstallRun.progress(ten, 0, size).steps[0].downloadedBytes)
    }
}
