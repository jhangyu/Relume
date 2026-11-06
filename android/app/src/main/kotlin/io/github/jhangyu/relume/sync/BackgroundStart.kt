package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.declineKey
import io.github.jhangyu.relume.install.seenKey

/** Background-start decision (spec 9.5). Pure. */
object BackgroundStart {
    /**
     * Per app: the server [plan] (built on the phone's package report and APK hashes; receipts fill in for older records)
     * without the builds the user declined in the background ([declined], app to [declineKey] of the download, `InstallRun.declined`)
     * and the items that need them; null when no app is left to install. No batch marker: an app that failed, was cancelled before it was shown
     * or sat in a replaced queued plan is planned again by the next check, whatever happened to the others. An app the
     * phone already has at the planned build stays in: the worker records it as installed without installing (its
     * receipt is history), right before the step (`VerifyRules.atTarget`).
     */
    fun backgroundPlan(plan: InstallPlan, declined: Map<String, String>): InstallPlan? {
        fun isDeclined(s: PlanStep) = s.download != null && declined[seenKey(s.kind, s.refId)] == declineKey(s.download)
        val declinedProjects = plan.steps.filter { it.kind == StepKind.PROJECT && isDeclined(it) }.map { it.refId }.toSet()
        val steps = plan.steps
            .filterNot { isDeclined(it) || it.action == PlanAction.INSTALL && it.needs.any { p -> p in declinedProjects } }
        return plan.copy(steps = steps).takeIf { p -> p.steps.any { it.action == PlanAction.INSTALL } }
    }
}
