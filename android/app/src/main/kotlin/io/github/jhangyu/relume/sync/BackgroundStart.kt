package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.seenKey

/** Background-start decision (spec 9.5). Pure. */
object BackgroundStart {
    /**
     * Per app: the server [plan] with every install step whose app is already at its target on the phone ([atTarget])
     * turned into `up_to_date`; null when no app is left to install. No batch marker: an app that failed, was cancelled
     * or sat in a replaced queued plan is planned again by the next check, whatever happened to the others. A build the
     * user declined in the background ([declined], app to download sha256, `InstallRun.declined`) is left out, with the
     * items that need it: the background does not ask for it again until a newer build or a UI request.
     */
    fun backgroundPlan(plan: InstallPlan, declined: Map<String, String>, atTarget: (PlanStep) -> Boolean): InstallPlan? {
        fun isDeclined(s: PlanStep) = s.download != null && declined[seenKey(s.kind, s.refId)] == s.download.sha256
        val declinedProjects = plan.steps.filter { it.kind == StepKind.PROJECT && isDeclined(it) }.map { it.refId }.toSet()
        val steps = plan.steps
            .filterNot { isDeclined(it) || it.action == PlanAction.INSTALL && it.needs.any { p -> p in declinedProjects } }
            .map { if (it.action == PlanAction.INSTALL && atTarget(it)) it.copy(action = PlanAction.UP_TO_DATE, download = null) else it }
        return plan.copy(steps = steps).takeIf { p -> p.steps.any { it.action == PlanAction.INSTALL } }
    }
}
