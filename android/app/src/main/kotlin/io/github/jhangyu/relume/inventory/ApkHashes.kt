package io.github.jhangyu.relume.inventory

import android.content.Context
import android.content.pm.PackageInfo
import io.github.jhangyu.relume.install.ApkVerifier
import java.io.File

/**
 * sha256 of each tracked app's installed base APK, reported in sync (decision 15). Cached per package: an update or
 * reinstall changes lastUpdateTime (and on Android 11+ the random /data/app dir), so either change re-hashes.
 * ponytail: entries of packages that left are never pruned; the tracked list is a handful of packages.
 */
object ApkHashes {
    private const val PREFS = "relume-apk-hashes"
    private const val SEP = "\n"

    /** Blocking file read on a cache miss: call off the main thread. */
    fun of(context: Context, info: PackageInfo): String {
        val dir = checkNotNull(info.applicationInfo) { "package ${info.packageName}: PackageInfo.applicationInfo is null" }.sourceDir
        val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        cachedHash(prefs.getString(info.packageName, null), dir, info.lastUpdateTime)?.let { return it }
        val sha = ApkVerifier.fileSha256(File(dir))
        val saved = prefs.edit().putString(info.packageName, entry(dir, info.lastUpdateTime, sha)).commit()
        check(saved) { "apk hash cache: SharedPreferences '$PREFS' commit failed for ${info.packageName}" }
        return sha
    }

    internal fun entry(sourceDir: String, lastUpdateTime: Long, sha256: String) = listOf(sourceDir, lastUpdateTime, sha256).joinToString(SEP)

    internal fun cachedHash(stored: String?, sourceDir: String, lastUpdateTime: Long): String? {
        val parts = stored?.split(SEP) ?: return null
        return if (parts.size == 3 && parts[0] == sourceDir && parts[1] == lastUpdateTime.toString()) parts[2] else null
    }
}
