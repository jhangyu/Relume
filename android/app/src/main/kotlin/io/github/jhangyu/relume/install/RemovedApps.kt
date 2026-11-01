package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind

/** Assigned item and project ids to unassign. */
data class Removed(val items: Set<Int>, val projects: Set<Int>) {
    val any get() = items.isNotEmpty() || projects.isNotEmpty()
}

/** Key of an assigned item or project in the phone's "seen installed" set (stored in prefs). */
fun seenKey(kind: StepKind, id: Int) = "${kind.name}:$id"

/**
 * The phone's set of assigned apps it has reported installed while they were assigned, after the sync that returned
 * [device]. Every sync path updates it, so a sync from the UI or the install worker never loses a removal before the
 * periodic check sees it. An app is added when the report has it; it is dropped when it is no longer assigned (so an app
 * assigned again is not in the set until it is seen installed again) or when the [lastRun] left it unfinished while it
 * is absent (a signature reinstall uninstalls on purpose, a failed install is pending): such an app is never unassigned.
 * Absence alone never drops an entry. Sign-in and sign-out clear the set.
 */
fun seenInstalled(seen: Set<String>, device: DeviceView, lastRun: RunState?): Set<String> {
    val unfinished = lastRun?.steps.orEmpty().filter { it.state != StepState.INSTALLED && it.state != StepState.UP_TO_DATE }
        .map { seenKey(it.step.kind, it.step.refId) }.toSet()
    val assigned = device.assignments.map { seenKey(StepKind.ITEM, it.itemId) to (it.installedReported != null) } +
        device.projects.filter { it.assigned }.map { seenKey(StepKind.PROJECT, it.projectId) to (it.installedReported != null) }
    return assigned.filter { (key, present) -> present || (key in seen && key !in unfinished) }.map { it.first }.toSet()
}

/**
 * Assigned apps the user removed from the phone: seen installed while assigned ([seen], already updated by this sync)
 * and absent from the current report ([device]). The plan must offer a real install of it: an error step names the
 * source package, not the built one, so its absence proves nothing.
 */
fun removedFromPhone(seen: Set<String>, device: DeviceView, plan: InstallPlan): Removed {
    val offered = plan.steps.filter { it.action == PlanAction.INSTALL }.map { seenKey(it.kind, it.refId) }.toSet()
    fun gone(kind: StepKind, id: Int, isPresent: Boolean) = seenKey(kind, id).let { !isPresent && it in seen && it in offered }
    return Removed(
        items = device.assignments.filter { gone(StepKind.ITEM, it.itemId, it.installedReported != null) }
            .map { it.itemId }.toSet(),
        projects = device.projects.filter { it.assigned && gone(StepKind.PROJECT, it.projectId, it.installedReported != null) }
            .map { it.projectId }.toSet(),
    )
}
