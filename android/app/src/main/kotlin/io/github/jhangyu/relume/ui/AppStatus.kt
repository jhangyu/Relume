package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.FollowedApp
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.PlanStep

internal const val STATE_BUILDING = "building"
internal const val STATE_QUEUED = "queued"
internal const val STATE_FAILED = "failed"
internal const val STATE_NEEDS_URL = "needs_download_url"
internal const val STATE_UP_TO_DATE = "up_to_date"
internal const val STATE_UPDATE_AVAILABLE = "update_available"
internal const val STATE_NOT_BUILT = "not_built"

internal const val LOOKUP_AMBIGUOUS = "ambiguous"
internal const val LOOKUP_BLOCKED = "blocked"
internal const val LOOKUP_NOT_FOUND = "not_found"
internal const val LOOKUP_PENDING = "pending"
internal const val LOOKUP_FOUND = "found"
internal const val LOOKUP_MANUAL = "manual"
internal const val LOOKUP_FAILED = "failed"

/** Apps card fix for a missing download URL: paste one, or pick one of the APKMirror candidates. */
enum class Lookup { NeedsUrl, PickOne }

/** The server builds this app now or has its build queued. */
internal val AppObject.building: Boolean get() = status.state == STATE_BUILDING || status.state == STATE_QUEUED

/** What the phone knows about one enabled app: the built APK it would install, that package on the phone, its patch version from the server's install record. */
class AppFacts(val app: AppObject, val target: InstallTarget?, val installed: InstalledPackage?, val installedPatch: String?) {
    val building: Boolean get() = app.building

    /** The card's one action (spec install-states 1); null while nothing is built. */
    val action: CardAction? get() = target?.let { cardAction(it, installed, installedPatch) }

    /** Download-source problem the Apps card offers a fix for (spec 5.1): null when the app has its URL. */
    val lookup: Lookup?
        get() = when {
            app.download.state == LOOKUP_AMBIGUOUS && app.download.candidates.isNotEmpty() -> Lookup.PickOne
            app.status.state == STATE_NEEDS_URL -> Lookup.NeedsUrl
            else -> null
        }

    /** One line under the card head: build progress, failure, why the download URL is missing, or the APKPure fallback. */
    val statusLine: String?
        get() {
            val s = app.status
            val d = app.download
            return when (s.state) {
                STATE_BUILDING -> s.progress?.let { p -> p.index?.let { "${p.step}, step $it of ${p.count}" } ?: p.step } ?: "Building"
                STATE_FAILED -> s.failDetail ?: s.failCode ?: "Build failed"
                STATE_NEEDS_URL -> d.error ?: when (d.state) {
                    LOOKUP_AMBIGUOUS -> "${d.candidates.size} APKMirror pages match"
                    LOOKUP_BLOCKED -> "APKMirror blocked the lookup${(d.blockedUntil ?: d.retryAt)?.let { ", retry after ${formatTime(it)}" }.orEmpty()}"
                    LOOKUP_NOT_FOUND -> "No APKMirror page found"
                    LOOKUP_PENDING -> "APKMirror lookup on the next update run"
                    else -> "Needs a download URL"
                }
                else -> app.downloadSource?.takeIf { it.actual != it.selected }?.let { "Downloads from APKPure (no APKMirror listing)" }
            }
        }
}


/** The one action of a card's phone row (spec install-states 1), shared by item and followed-project cards. */
enum class CardAction { Install, Reinstall, Upgrade, Installed }

/** What an install would put on the phone, read from the built APK (its package can differ from the source app's). */
data class InstallTarget(
    val packageName: String,
    val versionCode: Long?,
    val versionName: String?,
    val patchVersion: String?,
    val signerSha256: String?,
)

/**
 * Spec install-states 1, conditions in order. [installed] is looked up by [InstallTarget.packageName]; [installedPatch] is the
 * patch version of the server's install record (null = none, not compared).
 */
internal fun cardAction(target: InstallTarget, installed: InstalledPackage?, installedPatch: String?): CardAction {
    if (installed == null) return CardAction.Install
    val signer = target.signerSha256
    if (signer != null && !installed.signerSha256.equals(signer, ignoreCase = true)) return CardAction.Reinstall
    val code = target.versionCode
    if (code != null && installed.versionCode < code) return CardAction.Upgrade
    val patch = target.patchVersion
    if (installedPatch != null && patch != null && versionOlder(installedPatch, patch)) return CardAction.Upgrade
    return CardAction.Installed
}

private val VERSION_SEPARATORS = Regex("[.\\-+_]")

/** True when [a] sorts before [b] under the server's `catalog.version_key` (local/webui/catalog.py). */
internal fun versionOlder(a: String, b: String): Boolean {
    val x = versionKey(a)
    val y = versionKey(b)
    for (i in 0 until minOf(x.size, y.size)) {
        if (x[i] != y[i]) return x[i] < y[i]
    }
    return x.size < y.size
}

/** One leading v/V dropped; a part of digits is its number, any other part -1; a trailing 0 ranks a release above its prerelease. */
private fun versionKey(v: String): List<Long> {
    val s = if (v.startsWith("v") || v.startsWith("V")) v.substring(1) else v
    // ponytail: a digit run beyond Long saturates to MAX (Python ints are unbounded); no real tag is that long.
    return s.split(VERSION_SEPARATORS).map { p -> if (p.isNotEmpty() && p.all { it in '0'..'9' }) p.toLongOrNull() ?: Long.MAX_VALUE else -1L } + 0L
}

/** The built APK of an item card; null while nothing is built. */
internal val AppObject.target: InstallTarget?
    get() = build?.let { InstallTarget(it.packageName, it.versionCode, it.versionName, it.patchVersion, it.signerSha256) }

/** A followed project's install target: the plan step's file (phone ABI) when planned, else the first listed file. */
internal fun FollowedApp.target(step: PlanStep?): InstallTarget? {
    val d = step?.download
    if (step != null && d != null) return InstallTarget(step.packageName, d.versionCode, d.versionName, d.patchVersion, d.signerSha256)
    val pkg = packageName ?: return null
    if (blocked || files.isEmpty()) return null
    val f = files.first()
    return InstallTarget(pkg, f.versionCode, f.versionName, null, signerSha256)
}
