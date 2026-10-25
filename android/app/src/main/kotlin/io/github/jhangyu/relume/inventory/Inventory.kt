package io.github.jhangyu.relume.inventory

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.pm.PackageInfoCompat
import io.github.jhangyu.relume.BuildConfig
import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.RelumeApi
import io.github.jhangyu.relume.api.SyncDevice
import io.github.jhangyu.relume.api.SyncReport
import io.github.jhangyu.relume.install.VerifyRules

/** Result of `POST /api/me/sync`, plus the package list that was reported. */
data class SyncResult(
    val device: DeviceView,
    val trackedPackages: List<String>,
    val plan: InstallPlan,
    val reported: List<InstalledPackage>,
)

/** What is installed on this phone for the server's tracked packages (spec 9.6). No package broadcast receiver. */
object Inventory {

    fun read(context: Context, tracked: List<String>): List<InstalledPackage> = tracked.mapNotNull { installed(context, it) }

    /** Null = not installed. */
    fun installed(context: Context, packageName: String): InstalledPackage? = try {
        packageFacts(context.packageManager.getPackageInfo(packageName, PackageManager.GET_SIGNING_CERTIFICATES))
    } catch (e: PackageManager.NameNotFoundException) {
        // The API's defined answer for "not installed" (spec 9.6): the package is omitted.
        null
    }

    /** True when installing [packageName] through this app prompts once: not installed, or another installer of record (spec 8.3). */
    fun needsConfirmation(context: Context, packageName: String): Boolean {
        if (installed(context, packageName) == null) return true
        // getInstallerPackageName is deprecated in API 30 but works on 29–37; its API 30 replacement would need a version branch (spec 8.2).
        @Suppress("DEPRECATION")
        val installer = context.packageManager.getInstallerPackageName(packageName)
        return installer != context.packageName
    }

    /** Package name, long version code, version name and SHA-256 of signer #1 of an installed package or an archive. */
    fun packageFacts(info: PackageInfo): InstalledPackage {
        val signers = checkNotNull(info.signingInfo) { "package ${info.packageName}: PackageInfo.signingInfo is null" }
            .apkContentsSigners
        check(!signers.isNullOrEmpty()) { "package ${info.packageName}: no signing certificate" }
        return InstalledPackage(
            packageName = info.packageName,
            versionCode = PackageInfoCompat.getLongVersionCode(info),
            versionName = info.versionName,
            signerSha256 = VerifyRules.sha256Hex(signers[0].toByteArray()),
        )
    }

    fun deviceFacts() = SyncDevice(
        model = Build.MODEL,
        sdk = Build.VERSION.SDK_INT,
        androidRelease = Build.VERSION.RELEASE,
        abiList = Build.SUPPORTED_ABIS.toList(),
        appVersionCode = BuildConfig.VERSION_CODE.toLong(),
    )

    /** `GET /api/me` for the tracked list, read the packages, then `POST /api/me/sync`. */
    suspend fun sync(context: Context, api: RelumeApi): SyncResult {
        val tracked = api.me().trackedPackages
        val reported = read(context, tracked)
        val r = api.sync(SyncReport(deviceFacts(), reported))
        return SyncResult(r.device, r.trackedPackages, r.plan, reported)
    }
}
