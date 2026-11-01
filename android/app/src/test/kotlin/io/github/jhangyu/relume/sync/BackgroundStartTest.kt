package io.github.jhangyu.relume.sync

import android.content.pm.PackageInstaller
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.ErrorCodes
import io.github.jhangyu.relume.install.InstallResult
import io.github.jhangyu.relume.install.InstallRun
import io.github.jhangyu.relume.install.Origin
import io.github.jhangyu.relume.install.RunState
import io.github.jhangyu.relume.install.StepOutcome
import io.github.jhangyu.relume.install.StepState
import io.github.jhangyu.relume.install.VerifyRules
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
        // the phone: A is at its target now, B is not; the server plan may still list both
        val atTarget = { s: PlanStep -> s.refId == 1 }
        assertEquals(StepState.FAILED, ended.steps[1].state)
        val next = BackgroundStart.backgroundPlan(plan(a, b), emptyMap(), atTarget)
        assertEquals(listOf(2), installs(next))
        assertEquals(StepState.UP_TO_DATE, InstallRun.create(next!!, Origin.BACKGROUND, ended).steps[0].state)
        // every app at its target: no run
        assertNull(BackgroundStart.backgroundPlan(plan(a, b), emptyMap()) { true })
        assertNull(BackgroundStart.backgroundPlan(plan(step(StepKind.ITEM, 3, action = PlanAction.UP_TO_DATE)), emptyMap(), atTarget))
    }

    @Test
    fun aQueuedPlanReplacedByAUiRequestIsPlannedAgain() {
        val waiting = checkNotNull(InstallRun.move(InstallRun.create(plan(step(StepKind.ITEM, 5)), Origin.UI, null), 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 7))
        val queued = InstallRun.admit(waiting, plan(a, b), Origin.BACKGROUND, setOf(7), workPending = false) { error("not closed") }.run
        val replaced = InstallRun.admit(queued, plan(step(StepKind.ITEM, 9)), Origin.UI, setOf(7), workPending = false) { error("not closed") }.run
        assertEquals(listOf(9), replaced.queued?.plan?.steps?.map { it.refId })
        // nothing of A and B was installed: the next check plans both, with no memory of the replaced plan
        assertEquals(listOf(1, 2), installs(BackgroundStart.backgroundPlan(plan(a, b), emptyMap()) { false }))
    }

    @Test
    fun aRunThatStartedButHasNotFinishedCountsNothingHandled() {
        val run = InstallRun.create(plan(a, b), Origin.BACKGROUND, null)
        val committed = checkNotNull(InstallRun.move(run, 0, setOf(StepState.PENDING), StepState.COMMITTED, 7))
        val running = checkNotNull(InstallRun.move(committed, 1, setOf(StepState.PENDING), StepState.DOWNLOADING))
        // the phone has neither build yet: both are planned, the started run counts for nothing
        assertEquals(listOf(1, 2), installs(BackgroundStart.backgroundPlan(plan(a, b), InstallRun.declined(running)) { false }))
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
        // the check plans exactly that app
        val atA = { s: PlanStep -> VerifyRules.atTarget(checkNotNull(s.download), same) { if (s.refId == 1) d.sha256 else "ef".repeat(32) } }
        assertEquals(listOf(2), installs(BackgroundStart.backgroundPlan(plan(a, b), emptyMap(), atA)))
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
        assertEquals(listOf(3), installs(BackgroundStart.backgroundPlan(plan(a, b, c), InstallRun.declined(ended)) { false }))
        // the record outlives the run that holds the declined steps
        val next = InstallRun.create(plan(c), Origin.BACKGROUND, ended)
        assertEquals(InstallRun.declined(ended), InstallRun.declined(next))
        // a newer build of A is asked
        val newer = a.copy(download = checkNotNull(a.download).copy(sha256 = "ef".repeat(32)))
        assertEquals(listOf(1, 3), installs(BackgroundStart.backgroundPlan(plan(newer, b, c), InstallRun.declined(next)) { false }))
        // a sheet cancel of a background run declines too; a UI run's cancel is no background decline
        val cancelled = InstallRun.cancelAll(InstallRun.create(plan(c), Origin.BACKGROUND, null))
        assertEquals(setOf(seenKey(StepKind.ITEM, 3)), InstallRun.declined(cancelled).keys)
        val ui = checkNotNull(InstallRun.move(InstallRun.create(plan(c), Origin.UI, null), 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 8))
        assertTrue(InstallRun.declined(InstallRun.finish(ui, 0, aborted)).isEmpty())
        // a declined project leaves out the items that need it
        val p = step(StepKind.PROJECT, 4)
        val needsP = step(StepKind.ITEM, 5, needs = listOf(4))
        val declinedP = mapOf(seenKey(StepKind.PROJECT, 4) to checkNotNull(p.download).sha256)
        assertNull(BackgroundStart.backgroundPlan(plan(p, needsP), declinedP) { false })
    }

    @Test
    fun aUiInstallClearsTheDeclinedBuild() {
        val ended = declinedRun()
        // the user taps Install on A; a queued UI request clears it the same way when it starts (dequeue creates the run)
        val ui = InstallRun.create(plan(a), Origin.UI, ended)
        assertEquals(setOf(seenKey(StepKind.ITEM, 2)), InstallRun.declined(ui).keys)
        assertEquals(listOf(1), installs(BackgroundStart.backgroundPlan(plan(a, b), InstallRun.declined(ui)) { false }))
    }
}
