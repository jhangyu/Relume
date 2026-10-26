package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep

internal const val STATE_BUILDING = "building"
internal const val STATE_QUEUED = "queued"
internal const val STATE_FAILED = "failed"
internal const val STATE_NEEDS_URL = "needs_download_url"

/** What this phone has of an app. */
enum class PhoneState { NotInstalled, Update, Blocked, Installed }

/** What the phone knows about one enabled app: its installed package, the server plan step (null if not assigned). */
class AppFacts(val app: AppObject, val installed: InstalledPackage?, val step: PlanStep?) {
    /** Version the install would put on the phone. */
    val target: String? get() = step?.download?.versionName ?: app.appVersion

    val building: Boolean get() = app.status.state == STATE_BUILDING || app.status.state == STATE_QUEUED

    val phone: PhoneState
        get() = when {
            step?.action == PlanAction.INSTALL -> if (installed == null) PhoneState.NotInstalled else PhoneState.Update
            step?.action == PlanAction.ERROR -> PhoneState.Blocked
            step?.action == PlanAction.UP_TO_DATE -> PhoneState.Installed
            installed == null -> PhoneState.NotInstalled
            app.appVersion != null && app.appVersion != installed.versionName -> PhoneState.Update
            else -> PhoneState.Installed
        }

    /** An install can start: the plan offers one, or a build exists and the app is not yet planned. */
    val installable: Boolean
        get() = !building && (phone == PhoneState.NotInstalled || phone == PhoneState.Update) &&
            (step?.action == PlanAction.INSTALL || (step == null && app.appVersion != null))
}
