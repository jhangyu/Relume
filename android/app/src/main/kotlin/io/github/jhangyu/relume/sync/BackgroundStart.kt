package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep

/** Background-start decision (spec 9.5). Pure. */
object BackgroundStart {
    /**
     * Per app: the server [plan] with every install step whose app is already at its target on the phone ([atTarget])
     * turned into `up_to_date`; null when no app is left to install. No batch marker: an app that failed, was cancelled
     * or sat in a replaced queued plan is planned again by the next check, whatever happened to the others.
     */
    fun backgroundPlan(plan: InstallPlan, atTarget: (PlanStep) -> Boolean): InstallPlan? {
        val steps = plan.steps.map { if (it.action == PlanAction.INSTALL && atTarget(it)) it.copy(action = PlanAction.UP_TO_DATE, download = null) else it }
        return plan.copy(steps = steps).takeIf { p -> p.steps.any { it.action == PlanAction.INSTALL } }
    }
}
