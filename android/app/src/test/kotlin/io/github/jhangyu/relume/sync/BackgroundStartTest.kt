package io.github.jhangyu.relume.sync

import android.content.pm.PackageInstaller
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.ReceiptResult
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.ErrorCodes
import io.github.jhangyu.relume.install.InstallResult
import io.github.jhangyu.relume.install.InstallRun
import io.github.jhangyu.relume.install.Origin
import io.github.jhangyu.relume.install.RunState
import io.github.jhangyu.relume.install.StepOutcome
import io.github.jhangyu.relume.install.StepState
import io.github.jhangyu.relume.install.VerifyRules
import io.github.jhangyu.relume.install.atTargetOutcome
import io.github.jhangyu.relume.install.declineKey
import io.github.jhangyu.relume.install.download
import io.github.jhangyu.relume.install.plan
import io.github.jhangyu.relume.install.seenKey
import io.github.jhangyu.relume.install.step
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The background check plans each app on its own: exactly the apps not installed at their planned version. */
class BackgroundStartTest {
    private val a = step(StepKind.ITEM, 1)
    private val b = step(StepKind.ITEM, 2)

    private fun installs(p: io.github.jhangyu.relume.api.InstallPlan?) = p?.steps?.filter { it.action == PlanAction.INSTALL }?.map { it.refId }

    @Test
    fun aRunWhereOneAppInstallsAndOneFailsPlansOnlyTheFailedOne() {
        val run = InstallRun.create(plan(a, b), Origin.BACKGROUND, null)
        val ended = InstallRun.finish(
            InstallRun.finish(run, 0, InstallResult.from(PackageInstaller.STATUS_SUCCESS, null)),
            1, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "x"),
        )
        assertTrue("A's install receipt is due", ended.steps[0].receiptDue)
        assertEquals(StepState.FAILED, ended.steps[1].state)
        // A's receipt makes the server plan it up_to_date (on the phone's report); B is planned again
        val aDone = step(StepKind.ITEM, 1, action = PlanAction.UP_TO_DATE)
        assertEquals(listOf(2), installs(BackgroundStart.backgroundPlan(plan(aDone, b), InstallRun.declined(ended))))
        assertNull(BackgroundStart.backgroundPlan(plan(aDone), InstallRun.declined(ended)))
    }

    @Test
    fun anAppAlreadyAtTargetIsRecordedAsInstalledWithoutInstalling() {
        // the server lacks A's install record, so it plans A; the phone has the build: the step stays planned once
        assertEquals(listOf(1), installs(BackgroundStart.backgroundPlan(plan(a), emptyMap())))
        val skipped = InstallRun.finish(InstallRun.create(plan(a), Origin.BACKGROUND, null), 0, atTargetOutcome)
        assertEquals(StepState.INSTALLED, skipped.steps[0].state)
        assertTrue("the receipt tells the server", skipped.steps[0].receiptDue)
        assertEquals(ReceiptResult.INSTALLED, InstallRun.receipt(skipped.steps[0]).result)
        assertNull("nothing left to run", skipped.nextRunnable())
    }

    @Test
    fun aQueuedPlanReplacedByAUiRequestIsPlannedAgain() {
        val waiting = checkNotNull(InstallRun.move(InstallRun.create(plan(step(StepKind.ITEM, 5)), Origin.UI, null), 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 7))
        val queued = InstallRun.admit(waiting, plan(a, b), Origin.BACKGROUND, setOf(7), workPending = false) { error("not closed") }.run
        val replaced = InstallRun.admit(queued, plan(step(StepKind.ITEM, 9)), Origin.UI, setOf(7), workPending = false) { error("not closed") }.run
        assertEquals(listOf(9), replaced.queued?.plan?.steps?.map { it.refId })
        // nothing of A and B was installed: the next check plans both, with no memory of the replaced plan
        assertEquals(listOf(1, 2), installs(BackgroundStart.backgroundPlan(plan(a, b), emptyMap())))
    }

    @Test
    fun aRunThatStartedButHasNotFinishedCountsNothingHandled() {
        val run = InstallRun.create(plan(a, b), Origin.BACKGROUND, null)
        val committed = checkNotNull(InstallRun.move(run, 0, setOf(StepState.PENDING), StepState.COMMITTED, 7))
        val running = checkNotNull(InstallRun.move(committed, 1, setOf(StepState.PENDING), StepState.DOWNLOADING))
        // the phone has neither build yet: both are planned, the started run counts for nothing
        assertEquals(listOf(1, 2), installs(BackgroundStart.backgroundPlan(plan(a, b), InstallRun.declined(running))))
    }

    @Test
    fun aPatchRebuildWithTheSameVersionCodeIsPlannedOnlyWhenTheInstalledApkDiffers() {
        val d = download(StepKind.ITEM, 1)
        val same = InstalledPackage(packageName = "p", versionCode = d.versionCode, versionName = "1.0", signerSha256 = d.signerSha256.uppercase())
        assertTrue("the installed APK is the planned file", VerifyRules.atTarget(d, same) { d.sha256.uppercase() })
        assertFalse("patch-only rebuild, same version code", VerifyRules.atTarget(d, same) { "ef".repeat(32) })
        assertFalse("APK gone meanwhile", VerifyRules.atTarget(d, same) { null })
        val noHash = { error("hashed although version or signer already differ") }
        assertFalse("not installed", VerifyRules.atTarget(d, null, noHash))
        assertFalse("older version", VerifyRules.atTarget(d, same.copy(versionCode = d.versionCode - 1), noHash))
        assertFalse("other signer", VerifyRules.atTarget(d, same.copy(signerSha256 = "00".repeat(32)), noHash))
    }

    @Test
    fun aLostSessionCountsAsInstalledOnlyWhenThePhoneReplacedThePackage() {
        val d = download(StepKind.ITEM, 1)
        val at = InstalledPackage(packageName = "p", versionCode = d.versionCode, versionName = "1.0", signerSha256 = d.signerSha256)
        assertTrue("not installed before", VerifyRules.installedBySession(d, at, lastUpdateTime = 20, priorUpdateTime = null))
        assertTrue("replaced after the commit", VerifyRules.installedBySession(d, at, lastUpdateTime = 20, priorUpdateTime = 10))
        assertFalse("same-version rebuild not applied", VerifyRules.installedBySession(d, at, lastUpdateTime = 10, priorUpdateTime = 10))
        assertFalse("not installed", VerifyRules.installedBySession(d, null, lastUpdateTime = null, priorUpdateTime = null))
        assertFalse("older version", VerifyRules.installedBySession(d, at.copy(versionCode = 4), lastUpdateTime = 20, priorUpdateTime = 10))
    }

    private val aborted = InstallResult.from(PackageInstaller.STATUS_FAILURE_ABORTED, null)

    /** Background run of A (confirmation cancelled), B (needs_uninstall left), C (download failed). */
    private fun declinedRun(): RunState {
        val c = step(StepKind.ITEM, 3)
        val run = InstallRun.create(plan(a, b, c), Origin.BACKGROUND, null)
        val awaiting = checkNotNull(InstallRun.move(run, 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 7))
        return InstallRun.finish(
            InstallRun.finish(InstallRun.finish(awaiting, 0, aborted), 1, StepOutcome(StepState.NEEDS_UNINSTALL, ErrorCodes.SIGNATURE_MISMATCH, "x")),
            2, StepOutcome(StepState.FAILED, ErrorCodes.HASH_MISMATCH, "x"),
        )
    }

    @Test
    fun aDeclinedBackgroundBuildIsNotAskedAgainButANewerBuildIs() {
        val c = step(StepKind.ITEM, 3)
        val ended = declinedRun()
        assertEquals(setOf(seenKey(StepKind.ITEM, 1), seenKey(StepKind.ITEM, 2)), InstallRun.declined(ended).keys)
        // next check, nothing at its target: only the failed C is asked again
        assertEquals(listOf(3), installs(BackgroundStart.backgroundPlan(plan(a, b, c), InstallRun.declined(ended))))
        // the record outlives the run that holds the declined steps
        val next = InstallRun.create(plan(c), Origin.BACKGROUND, ended)
        assertEquals(InstallRun.declined(ended), InstallRun.declined(next))
        // a re-patch of A at the same versions (another sha256) is not asked again
        val repatched = a.copy(download = checkNotNull(a.download).copy(sha256 = "ef".repeat(32)))
        assertEquals(listOf(3), installs(BackgroundStart.backgroundPlan(plan(repatched, b, c), InstallRun.declined(next))))
        // a newer build of A is asked
        val newer = a.copy(download = checkNotNull(a.download).copy(versionCode = checkNotNull(a.download).versionCode + 1))
        assertEquals(listOf(1, 3), installs(BackgroundStart.backgroundPlan(plan(newer, b, c), InstallRun.declined(next))))
        // a sheet Cancel of a background run declines only the confirmation that was up, not the steps never shown
        val shown = checkNotNull(InstallRun.move(InstallRun.create(plan(a, b, c), Origin.BACKGROUND, null), 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 9))
        val downloading = checkNotNull(InstallRun.move(shown, 1, setOf(StepState.PENDING), StepState.DOWNLOADING))
        val cancelled = InstallRun.cancelAll(downloading)
        assertEquals(listOf(StepState.CANCELLED, StepState.CANCELLED, StepState.CANCELLED), cancelled.steps.map { it.state })
        assertEquals(setOf(seenKey(StepKind.ITEM, 1)), InstallRun.declined(cancelled).keys)
        assertEquals(listOf(2, 3), installs(BackgroundStart.backgroundPlan(plan(a, b, c), InstallRun.declined(cancelled))))
        // a UI run's cancel is no background decline
        val ui = checkNotNull(InstallRun.move(InstallRun.create(plan(c), Origin.UI, null), 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 8))
        assertTrue(InstallRun.declined(InstallRun.finish(ui, 0, aborted)).isEmpty())
        // a declined project leaves out the items that need it
        val p = step(StepKind.PROJECT, 4)
        val needsP = step(StepKind.ITEM, 5, needs = listOf(4))
        val declinedP = mapOf(seenKey(StepKind.PROJECT, 4) to declineKey(checkNotNull(p.download)))
        assertNull(BackgroundStart.backgroundPlan(plan(p, needsP), declinedP))
    }

    @Test
    fun aUiInstallClearsTheDeclinedBuild() {
        val ended = declinedRun()
        // the user taps Install on A; a queued UI request clears it the same way when it starts (dequeue creates the run)
        val ui = InstallRun.create(plan(a), Origin.UI, ended)
        assertEquals(setOf(seenKey(StepKind.ITEM, 2)), InstallRun.declined(ui).keys)
        assertEquals(listOf(1), installs(BackgroundStart.backgroundPlan(plan(a, b), InstallRun.declined(ui))))
    }
}
