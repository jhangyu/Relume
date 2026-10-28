package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep

internal const val STATE_BUILDING = "building"
internal const val STATE_QUEUED = "queued"
internal const val STATE_FAILED = "failed"
internal const val STATE_NEEDS_URL = "needs_download_url"

internal const val LOOKUP_AMBIGUOUS = "ambiguous"

/** Apps card fix for a missing download URL: paste one, or pick one of the APKMirror candidates. */
enum class Lookup { NeedsUrl, PickOne }

/** What this phone has of an app. */
enum class PhoneState { NotInstalled, Update, Blocked, Installed }

/** Phone state from the plan step; without a step, from the installed package ([newer]: the server has another version). */
internal fun phoneState(step: PlanStep?, installed: InstalledPackage?, newer: Boolean): PhoneState = when {
    step?.action == PlanAction.INSTALL -> if (installed == null) PhoneState.NotInstalled else PhoneState.Update
    step?.action == PlanAction.ERROR -> PhoneState.Blocked
    step?.action == PlanAction.UP_TO_DATE -> PhoneState.Installed
    installed == null -> PhoneState.NotInstalled
    newer -> PhoneState.Update
    else -> PhoneState.Installed
}

/** What the phone knows about one enabled app: its installed package, the server plan step (null if not assigned). */
class AppFacts(val app: AppObject, val installed: InstalledPackage?, val step: PlanStep?) {
    /** Version the install would put on the phone. */
    val target: String? get() = step?.download?.versionName ?: app.appVersion

    val building: Boolean get() = app.status.state == STATE_BUILDING || app.status.state == STATE_QUEUED

    val phone: PhoneState
        get() = phoneState(step, installed, app.appVersion != null && app.appVersion != installed?.versionName)

    /** Download-source problem the Apps card offers a fix for (spec 5.1): null when the app has its URL. */
    val lookup: Lookup?
        get() = when {
            app.download.state == LOOKUP_AMBIGUOUS && app.download.candidates.isNotEmpty() -> Lookup.PickOne
            app.status.state == STATE_NEEDS_URL -> Lookup.NeedsUrl
            else -> null
        }

    /** One line under the card head: build progress, failure, or why the download URL is missing. */
    val statusLine: String?
        get() {
            val s = app.status
            val d = app.download
            return when (s.state) {
                STATE_BUILDING -> s.progress?.let { p -> p.index?.let { "${p.step}, step $it of ${p.count}" } ?: p.step } ?: "Building"
                STATE_FAILED -> s.failDetail ?: s.failCode ?: "Build failed"
                STATE_NEEDS_URL -> d.error ?: when (d.state) {
                    LOOKUP_AMBIGUOUS -> "${d.candidates.size} APKMirror pages match"
                    "blocked" -> "APKMirror blocked the lookup${d.retryAt?.let { ", retry after ${formatTime(it)}" }.orEmpty()}"
                    "not_found" -> "No APKMirror page found"
                    "pending" -> "APKMirror lookup on the next update run"
                    else -> "Needs a download URL"
                }
                else -> null
            }
        }

    /** An install can start: the plan offers one, or a build exists and the app is not yet planned. */
    val installable: Boolean
        get() = !building && (phone == PhoneState.NotInstalled || phone == PhoneState.Update) &&
            (step?.action == PlanAction.INSTALL || (step == null && app.appVersion != null))
}
