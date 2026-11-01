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
import io.github.jhangyu.relume.install.StepOutcome
import io.github.jhangyu.relume.install.StepState
import io.github.jhangyu.relume.install.VerifyRules
import io.github.jhangyu.relume.install.download
import io.github.jhangyu.relume.install.plan
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
        val next = BackgroundStart.backgroundPlan(plan(a, b), atTarget)
        assertEquals(listOf(2), installs(next))
        assertEquals(StepState.UP_TO_DATE, InstallRun.create(next!!, Origin.BACKGROUND, ended).steps[0].state)
        // every app at its target: no run
        assertNull(BackgroundStart.backgroundPlan(plan(a, b)) { true })
        assertNull(BackgroundStart.backgroundPlan(plan(step(StepKind.ITEM, 3, action = PlanAction.UP_TO_DATE)), atTarget))
    }

    @Test
    fun aQueuedPlanReplacedByAUiRequestIsPlannedAgain() {
        val waiting = checkNotNull(InstallRun.move(InstallRun.create(plan(step(StepKind.ITEM, 5)), Origin.UI, null), 0, setOf(StepState.PENDING), StepState.AWAITING_USER, 7))
        val queued = InstallRun.admit(waiting, plan(a, b), Origin.BACKGROUND, setOf(7), workPending = false) { error("not closed") }.run
        val replaced = InstallRun.admit(queued, plan(step(StepKind.ITEM, 9)), Origin.UI, setOf(7), workPending = false) { error("not closed") }.run
        assertEquals(listOf(9), replaced.queued?.plan?.steps?.map { it.refId })
        // nothing of A and B was installed: the next check plans both, with no memory of the replaced plan
        assertEquals(listOf(1, 2), installs(BackgroundStart.backgroundPlan(plan(a, b)) { false }))
    }

    @Test
    fun atTargetIsTheInstalledVersionSignerAndPatch() {
        val d = download(StepKind.ITEM, 1).copy(patchVersion = "p2")
        val same = InstalledPackage(packageName = "p", versionCode = d.versionCode, versionName = "1.0", signerSha256 = d.signerSha256.uppercase())
        assertTrue(VerifyRules.atTarget(d, same, "p2"))
        assertFalse("not installed", VerifyRules.atTarget(d, null, "p2"))
        assertFalse("older version", VerifyRules.atTarget(d, same.copy(versionCode = d.versionCode - 1), "p2"))
        assertFalse("other signer", VerifyRules.atTarget(d, same.copy(signerSha256 = "00".repeat(32)), "p2"))
        assertFalse("patch-only update", VerifyRules.atTarget(d, same, "p1"))
        assertFalse("no patch record", VerifyRules.atTarget(d, same, null))
        assertTrue("a project has no patch version", VerifyRules.atTarget(d.copy(patchVersion = null), same, null))
    }
}
