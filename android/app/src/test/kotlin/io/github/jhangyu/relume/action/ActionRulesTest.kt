package io.github.jhangyu.relume.action

import android.content.pm.PackageInstaller
import io.github.jhangyu.relume.api.ActionKind
import io.github.jhangyu.relume.api.ActionOutcome
import io.github.jhangyu.relume.api.ActionRef
import io.github.jhangyu.relume.api.ActionSource
import io.github.jhangyu.relume.api.ApiJson
import io.github.jhangyu.relume.api.DeviceAction
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.step
import kotlinx.serialization.SerializationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ActionRulesTest {
    private val ref = ActionRef(StepKind.ITEM, 4)
    private fun action(kind: ActionKind, source: ActionSource?, ref: ActionRef? = null) = DeviceAction(7, "com.x", kind, source, ref)
    private fun outcome(remove: Boolean, status: Int) = ActionRules.result(remove, status, "msg")?.let { it.result to it.errorCode }

    @Test fun installStatuses() {
        assertNull(ActionRules.result(false, PackageInstaller.STATUS_PENDING_USER_ACTION, null))
        assertEquals(ActionOutcome.DONE to null, outcome(false, PackageInstaller.STATUS_SUCCESS))
        assertEquals(ActionOutcome.CANCELLED to ActionRules.CANCELLED_BY_USER, outcome(false, PackageInstaller.STATUS_FAILURE_ABORTED))
        assertEquals(ActionOutcome.FAILED to ActionRules.SIGNATURE_MISMATCH, outcome(false, PackageInstaller.STATUS_FAILURE_CONFLICT))
        assertEquals(ActionOutcome.FAILED to ActionRules.INSUFFICIENT_STORAGE, outcome(false, PackageInstaller.STATUS_FAILURE_STORAGE))
        assertEquals(ActionOutcome.FAILED to ActionRules.INSTALLER_ERROR, outcome(false, PackageInstaller.STATUS_FAILURE_BLOCKED))
        assertEquals(ActionOutcome.FAILED to ActionRules.INSTALLER_ERROR, outcome(false, 99))
        assertEquals("STATUS_FAILURE_INVALID: msg", ActionRules.result(false, PackageInstaller.STATUS_FAILURE_INVALID, "msg")?.errorText)
    }

    @Test fun uninstallStatuses() {
        assertNull(ActionRules.result(true, PackageInstaller.STATUS_PENDING_USER_ACTION, null))
        assertEquals(ActionOutcome.DONE to null, outcome(true, PackageInstaller.STATUS_SUCCESS))
        assertEquals(ActionOutcome.CANCELLED to ActionRules.CANCELLED_BY_USER, outcome(true, PackageInstaller.STATUS_FAILURE_ABORTED))
        assertEquals(ActionOutcome.FAILED to ActionRules.UNINSTALL_FAILED, outcome(true, PackageInstaller.STATUS_FAILURE_CONFLICT))
        assertEquals(ActionOutcome.FAILED to ActionRules.UNINSTALL_FAILED, outcome(true, PackageInstaller.STATUS_FAILURE))
    }

    @Test fun routes() {
        assertEquals(Route.Remove, ActionRules.route(action(ActionKind.REMOVE, null)))
        assertEquals(Route.Build(ref), ActionRules.route(action(ActionKind.UPGRADE, ActionSource.BUILD, ref)))
        assertEquals(Route.PlayDirect, ActionRules.route(action(ActionKind.INSTALL, ActionSource.PLAY_DIRECT)))
        assertEquals(Route.PlayPage, ActionRules.route(action(ActionKind.INSTALL, ActionSource.PLAY_PAGE)))
    }

    @Test fun invalidCombinationsRejected() {
        val bad = listOf(
            action(ActionKind.REMOVE, ActionSource.PLAY_DIRECT),
            action(ActionKind.REMOVE, null, ref),
            action(ActionKind.INSTALL, null),
            action(ActionKind.UPGRADE, ActionSource.BUILD),
            action(ActionKind.INSTALL, ActionSource.PLAY_DIRECT, ref),
            action(ActionKind.INSTALL, ActionSource.PLAY_PAGE, ref),
        )
        for (a in bad) assertTrue("$a", ActionRules.route(a) is Route.Invalid)
    }

    @Test(expected = SerializationException::class)
    fun unknownKindRejected() {
        ApiJson.decodeFromString(DeviceAction.serializer(), """{"id":1,"package":"com.x","action":"reinstall","source":null,"ref":null}""")
    }

    @Test fun serverClosedStarts() {
        assertTrue(ActionRules.closedByServer(404))
        assertTrue(ActionRules.closedByServer(409))
        assertTrue(ActionRules.closedByServer(502))
        assertFalse(ActionRules.closedByServer(500))
        assertFalse(ActionRules.closedByServer(401))
    }

    @Test fun buildOutcomes() {
        fun step(action: PlanAction) = step(StepKind.ITEM, 4, action = action)
        assertNull(ActionRules.buildOutcome(ref, step(PlanAction.INSTALL)))
        assertEquals(ActionRules.DONE, ActionRules.buildOutcome(ref, step(PlanAction.UP_TO_DATE)))
        val error = ActionRules.buildOutcome(ref, step(PlanAction.ERROR))
        assertEquals(ActionOutcome.FAILED to ActionRules.INSTALLER_ERROR, error?.result to error?.errorCode)
        assertEquals("plan step ITEM 4: NOT_DOWNLOADED", error?.errorText)
        assertEquals(ActionRules.INSTALLER_ERROR, ActionRules.buildOutcome(ref, null)?.errorCode)
    }

    @Test fun sessionGone() {
        assertEquals(ActionRules.DONE, ActionRules.sessionGone(12, 12))
        assertEquals(ActionOutcome.FAILED, ActionRules.sessionGone(11, 12).result)
        assertEquals(ActionOutcome.FAILED, ActionRules.sessionGone(null, 12).result)
        assertEquals(ActionOutcome.FAILED, ActionRules.sessionGone(12, null).result)
    }
}
