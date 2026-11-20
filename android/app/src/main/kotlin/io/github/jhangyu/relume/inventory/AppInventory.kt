package io.github.jhangyu.relume.inventory

import android.app.AppOpsManager
import android.app.usage.StorageStatsManager
import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.os.Process
import androidx.core.content.pm.PackageInfoCompat
import androidx.core.graphics.drawable.toBitmap
import io.github.jhangyu.relume.api.InventoryApp
import io.github.jhangyu.relume.install.VerifyRules
import java.io.ByteArrayOutputStream
import java.io.IOException

/** Every user (non-system) app on this phone for App Manage, with sizes when usage access is granted (plan R1-F). */
object AppInventory {
    const val NO_USAGE_ACCESS = "usage access not granted"
    private const val ICON_PX = 96
    private const val PNG_QUALITY = 100
    /** Server rule for `label`: 1–200 characters. */
    private const val LABEL_MAX = 200
    /** Server rule for `size_reason`: 1–200 characters. */
    private const val REASON_MAX = 200

    /** [apps] in package order; [icons] maps each `icon_sha256` to its PNG for the upload the server asks for. */
    class Read(val apps: List<InventoryApp>, val icons: Map<String, ByteArray>)

    /** True when the user granted usage access (needed by [StorageStatsManager] for other packages). */
    fun usageAccess(context: Context): Boolean {
        val ops = context.getSystemService(AppOpsManager::class.java)
        return when (ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName)) {
            AppOpsManager.MODE_ALLOWED -> true
            // MODE_DEFAULT: the op defers to the permission grant itself.
            AppOpsManager.MODE_DEFAULT ->
                context.checkSelfPermission(android.Manifest.permission.PACKAGE_USAGE_STATS) == PackageManager.PERMISSION_GRANTED
            else -> false
        }
    }

    /** Blocking (sizes, icons): call off the main thread. */
    fun read(context: Context): Read {
        val pm = context.packageManager
        val stats = if (usageAccess(context)) context.getSystemService(StorageStatsManager::class.java) else null
        val icons = HashMap<String, ByteArray>()
        // getInstalledPackages(int) is deprecated in API 33 but works on 29–37; its replacement would need a version branch.
        @Suppress("DEPRECATION")
        val packages = pm.getInstalledPackages(0)
        val apps = packages.mapNotNull { info ->
            val ai = checkNotNull(info.applicationInfo) { "package ${info.packageName}: PackageInfo.applicationInfo is null" }
            if (ai.flags and ApplicationInfo.FLAG_SYSTEM != 0) return@mapNotNull null
            val png = iconPng(pm, ai)
            val sha = VerifyRules.sha256Hex(png)
            icons[sha] = png
            val sizes = sizes(stats, ai)
            InventoryApp(
                packageName = info.packageName,
                label = label(pm.getApplicationLabel(ai).toString(), info.packageName),
                versionCode = PackageInfoCompat.getLongVersionCode(info),
                versionName = info.versionName,
                appBytes = sizes.app,
                dataBytes = sizes.data,
                cacheBytes = sizes.cache,
                sizeReason = sizes.reason,
                iconSha256 = sha,
            )
        }.sortedBy { it.packageName }
        return Read(apps, icons)
    }

    // ponytail: a blank label shows the package name, as the launcher does; an overlong one is cut to the server limit.
    private fun label(text: String, packageName: String) = text.trim().ifEmpty { packageName }.take(LABEL_MAX)

    private fun iconPng(pm: PackageManager, ai: ApplicationInfo): ByteArray {
        val bitmap = pm.getApplicationIcon(ai).toBitmap(ICON_PX, ICON_PX, Bitmap.Config.ARGB_8888)
        val out = ByteArrayOutputStream()
        check(bitmap.compress(Bitmap.CompressFormat.PNG, PNG_QUALITY, out)) { "icon of ${ai.packageName}: PNG encoding failed" }
        return out.toByteArray()
    }

    private class Sizes(val app: Long?, val data: Long?, val cache: Long?, val reason: String?)

    /** StorageStats data bytes include the cache: [Sizes.data] is the rest, so app + data + cache is the total. */
    private fun sizes(stats: StorageStatsManager?, ai: ApplicationInfo): Sizes {
        if (stats == null) return Sizes(null, null, null, NO_USAGE_ACCESS)
        val s = try {
            stats.queryStatsForPackage(ai.storageUuid, ai.packageName, Process.myUserHandle())
        } catch (e: PackageManager.NameNotFoundException) {
            return Sizes(null, null, null, "storage stats of ${ai.packageName}: package gone while reading ($e)".take(REASON_MAX))
        } catch (e: IOException) {
            return Sizes(null, null, null, "storage stats of ${ai.packageName}: $e".take(REASON_MAX))
        }
        check(s.dataBytes >= s.cacheBytes) {
            "storage stats of ${ai.packageName}: expected data bytes >= cache bytes, got ${s.dataBytes} < ${s.cacheBytes}"
        }
        return Sizes(s.appBytes, s.dataBytes - s.cacheBytes, s.cacheBytes, null)
    }
}
