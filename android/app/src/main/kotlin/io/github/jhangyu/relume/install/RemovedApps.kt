package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.StepKind

/** Outcome of a step skipped because the user removed its app from the phone. */
val removedOutcome = StepOutcome(StepState.REMOVED, null, "removed from the phone: unassigned")

/**
 * The check right before install step [s] runs: the user removed its app when it was seen installed while assigned
 * ([seen]) and is not on the phone now ([onPhone]). An app this run uninstalled for a signature reinstall never counts.
 */
fun removedFromPhone(s: StepRun, seen: Set<String>, onPhone: Boolean): Boolean =
    !onPhone && !s.uninstalled && seenKey(s.step.kind, s.step.refId) in seen

/** Assigned item and project ids of [device] without [step]'s app: the body of the unassign `PUT`. */
fun assignmentsWithout(device: DeviceView, step: PlanStep): Pair<List<Int>, List<Int>> {
    val items = device.assignments.map { it.itemId }
    val projects = device.projects.filter { it.assigned }.map { it.projectId }
    return when (step.kind) {
        StepKind.ITEM -> (items - step.refId) to projects
        StepKind.PROJECT -> items to (projects - step.refId)
    }
}

/** Key of an assigned item or project in the phone's "seen installed" set (stored in prefs). */
fun seenKey(kind: StepKind, id: Int) = "${kind.name}:$id"

/**
 * The phone's set of assigned apps it has reported installed while they were assigned, after the sync that returned
 * [device]. Every sync path updates it, so a sync from the UI or the install worker never loses a removal before the
 * periodic check sees it. An app is added when the report has it; it is dropped when it is no longer assigned (so an app
 * assigned again is not in the set until it is seen installed again) or when it is absent after the [lastRun]'s signature
 * reinstall uninstalled it (`needs_uninstall`, or re-run and not installed yet): the next run installs it. Absence alone never drops an entry. Sign-in and sign-out clear the set.
 */
fun seenInstalled(seen: Set<String>, device: DeviceView, lastRun: RunState?): Set<String> {
    val unfinished = lastRun?.steps.orEmpty()
        .filter { it.state == StepState.NEEDS_UNINSTALL || it.uninstalled && it.state != StepState.INSTALLED }
        .map { seenKey(it.step.kind, it.step.refId) }.toSet()
    val assigned = device.assignments.map { seenKey(StepKind.ITEM, it.itemId) to (it.installedReported != null) } +
        device.projects.filter { it.assigned }.map { seenKey(StepKind.PROJECT, it.projectId) to (it.installedReported != null) }
    return assigned.filter { (key, present) -> present || (key in seen && key !in unfinished) }.map { it.first }.toSet()
}
