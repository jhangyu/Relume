package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind

/** Assigned item and project ids to unassign. */
data class Removed(val items: Set<Int>, val projects: Set<Int>) {
    val any get() = items.isNotEmpty() || projects.isNotEmpty()
}

/**
 * Assigned apps the user removed from the phone: an install receipt from this phone exists and the phone's report no
 * longer has the package. The plan must offer a real install of it: an error step names the source package, not the
 * built one, so its absence proves nothing. Never an app the [lastRun] left unfinished (a signature reinstall
 * uninstalls on purpose, a failed install is pending): only steps that ended installed or up to date clear an app.
 */
fun removedFromPhone(device: DeviceView, plan: InstallPlan, lastRun: RunState?): Removed {
    val unfinished = lastRun?.steps.orEmpty().filter { it.state != StepState.INSTALLED && it.state != StepState.UP_TO_DATE }
        .map { it.step.kind to it.step.refId }.toSet()
    val offered = plan.steps.filter { it.action == PlanAction.INSTALL }.map { it.kind to it.refId }.toSet()
    fun gone(kind: StepKind, id: Int, receipt: Boolean, reported: Boolean) =
        receipt && !reported && (kind to id) in offered && (kind to id) !in unfinished
    return Removed(
        items = device.assignments.filter { gone(StepKind.ITEM, it.itemId, it.installed != null, it.installedReported != null) }
            .map { it.itemId }.toSet(),
        projects = device.projects.filter { it.assigned && gone(StepKind.PROJECT, it.projectId, it.installedTag != null, it.installedReported != null) }
            .map { it.projectId }.toSet(),
    )
}
