package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction

/** Background-start decision (spec 9.5). Pure. */
object BackgroundStart {
    /** Install steps present and a revision the background has not started yet; an open install session queues it (InstallRunner.start). */
    fun shouldStart(plan: InstallPlan, lastBackgroundRevision: String?): Boolean =
        plan.revision != lastBackgroundRevision && plan.steps.any { it.action == PlanAction.INSTALL }
}
