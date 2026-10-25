package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction

/** Background-start decision (spec 9.5). Pure. */
object BackgroundStart {
    /** Install steps present, no run active, and a revision the background has not started yet. */
    fun shouldStart(plan: InstallPlan, runActive: Boolean, lastBackgroundRevision: String?): Boolean =
        !runActive && plan.revision != lastBackgroundRevision && plan.steps.any { it.action == PlanAction.INSTALL }
}
