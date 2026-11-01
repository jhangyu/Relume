package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.StepKind

/** Outcome of a step skipped because the user removed its app from the phone. */
val removedOutcome = StepOutcome(StepState.REMOVED, null, "removed from the phone: unassigned")

/**
 * Outcome of a step whose app the phone already has at the planned build (`VerifyRules.atTarget`): recorded as installed,
 * so its receipt and patch record are written; the server planned it because it lacks that install record.
 */
val atTargetOutcome = StepOutcome(StepState.INSTALLED, null, null)

/** Keys ([seenKey]) of every app [plan] installs: a UI request asks for all of them (the tapped app and its dependencies). */
fun requestedKeys(plan: InstallPlan): Set<String> =
    plan.steps.filter { it.action == PlanAction.INSTALL }.map { seenKey(it.kind, it.refId) }.toSet()

/**
 * The check right before install step [s] runs: the user removed its app when it was seen installed while assigned
 * ([seen]) and is not on the phone now ([onPhone]). A signature reinstall drops the app from [seen] when its uninstall
 * succeeds (InstallRunner), and a UI request drops every app it installs ([requestedKeys]), so neither counts.
 */
fun removedFromPhone(s: StepRun, seen: Set<String>, onPhone: Boolean): Boolean =
    !onPhone && seenKey(s.step.kind, s.step.refId) in seen

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
 * assigned again is not in the set until it is seen installed again) or when it is absent while the [lastRun]'s step
 * waits for its signature uninstall (`needs_uninstall`, removed outside the dialog): the next run installs it. Absence
 * alone never drops an entry. Sign-in and sign-out clear the set.
 */
fun seenInstalled(seen: Set<String>, device: DeviceView, lastRun: RunState?): Set<String> {
    val unfinished = lastRun?.steps.orEmpty()
        .filter { it.state == StepState.NEEDS_UNINSTALL }
        .map { seenKey(it.step.kind, it.step.refId) }.toSet()
    val assigned = device.assignments.map { seenKey(StepKind.ITEM, it.itemId) to (it.installedReported != null) } +
        device.projects.filter { it.assigned }.map { seenKey(StepKind.PROJECT, it.projectId) to (it.installedReported != null) }
    return assigned.filter { (key, present) -> present || (key in seen && key !in unfinished) }.map { it.first }.toSet()
}
