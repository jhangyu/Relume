package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.install.StartResult

/** Background-start decision (spec 9.5). Pure. */
object BackgroundStart {
    /** The background revision to keep after a start with [result]: handled only once its run started (a queued plan may be replaced). */
    fun recordedRevision(result: StartResult, plan: InstallPlan, last: String?): String? =
        if (result == StartResult.Started) plan.revision else last

    /** Install steps present and a revision the background has not started yet; an open install session queues it (InstallRunner.start). */
    fun shouldStart(plan: InstallPlan, lastBackgroundRevision: String?): Boolean =
        plan.revision != lastBackgroundRevision && plan.steps.any { it.action == PlanAction.INSTALL }
}
