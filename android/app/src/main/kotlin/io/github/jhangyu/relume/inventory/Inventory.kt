package io.github.jhangyu.relume.inventory

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.core.content.pm.PackageInfoCompat
import io.github.jhangyu.relume.BuildConfig
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.DeviceAction
import io.github.jhangyu.relume.api.DeviceView
import io.github.jhangyu.relume.api.IconUpload
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.RelumeApi
import io.github.jhangyu.relume.api.SyncDevice
import io.github.jhangyu.relume.api.SyncReport
import io.github.jhangyu.relume.api.SyncResponse
import io.github.jhangyu.relume.install.InstallRunner
import io.github.jhangyu.relume.install.VerifyRules
import io.github.jhangyu.relume.install.seenInstalled
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.Base64

/** Result of `POST /api/me/sync`, plus the package list that was reported. */
data class SyncResult(
    val device: DeviceView,
    /** The phone's seen-installed set after this sync (`install.seenInstalled`). */
    val seen: Set<String>,
    val trackedPackages: List<String>,
    val plan: InstallPlan,
    val reported: List<InstalledPackage>,
    /** App Manage actions the server handed out in this sync (delivered once). */
    val actions: List<DeviceAction>,
)

/** What is installed on this phone for the server's tracked packages (spec 9.6). No package broadcast receiver. */
object Inventory {

    /** Tracked packages on this phone with their base APK sha256 (decision 15). Hashing reads files: runs on IO. */
    suspend fun read(context: Context, tracked: List<String>): List<InstalledPackage> = withContext(Dispatchers.IO) {
        tracked.mapNotNull { pkg -> packageInfo(context, pkg)?.let { packageFacts(it).copy(apkSha256 = ApkHashes.of(context, it)) } }
    }

    /** The installed package with its signing certificates; null = not installed. */
    fun packageInfo(context: Context, packageName: String): PackageInfo? = try {
        context.packageManager.getPackageInfo(packageName, PackageManager.GET_SIGNING_CERTIFICATES)
    } catch (e: PackageManager.NameNotFoundException) {
        // The API's defined answer for "not installed" (spec 9.6): the package is omitted.
        null
    }

    /** Null = not installed. */
    fun installed(context: Context, packageName: String): InstalledPackage? = packageInfo(context, packageName)?.let { packageFacts(it) }

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

    fun deviceFacts(context: Context) = SyncDevice(
        deviceUid = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID),
        model = Build.MODEL,
        sdk = Build.VERSION.SDK_INT,
        androidRelease = Build.VERSION.RELEASE,
        abiList = Build.SUPPORTED_ABIS.toList(),
        appVersionCode = BuildConfig.VERSION_CODE.toLong(),
    )

    /**
     * `GET /api/me` for the tracked list, read the packages and the App Manage inventory, `POST /api/me/sync`, then update the
     * seen-installed set. A diff the server refuses (`accepted=false`) is resent once as the full list; the icons it asks for
     * are uploaded; the accepted rev and list become the next diff base. Serialised: the UI and both workers sync.
     */
    suspend fun sync(context: Context, api: RelumeApi): SyncResult = syncLock.withLock {
        val tracked = api.me().trackedPackages
        val reported = read(context, tracked)
        val apps = withContext(Dispatchers.IO) { AppInventory.read(context) }
        val snapshotFile = File(context.applicationContext.filesDir, SNAPSHOT_FILE)
        val first = InventoryDiff.report(withContext(Dispatchers.IO) { readSnapshot(snapshotFile) }, apps.apps)
        var r = api.sync(SyncReport(deviceFacts(context), reported, first))
        if (r.inventory?.accepted == false && !first.full) {
            r = api.sync(SyncReport(deviceFacts(context), reported, InventoryDiff.full(apps.apps)))
        }
        storeInventory(api, r, apps, snapshotFile)
        InstallRunner.loadState(context)
        val seen = (context.applicationContext as RelumeApp).prefs
            .updateSeenInstalled { seenInstalled(it, r.device, InstallRunner.state.value) }
        SyncResult(r.device, seen, r.trackedPackages, r.plan, reported, r.actions)
    }

    private const val SNAPSHOT_FILE = "app-inventory.json"
    private val syncLock = Mutex()
    private val SnapshotJson = Json { ignoreUnknownKeys = true }

    /** Uploads the icons the server lacks, then saves the accepted list as the next diff base. */
    private suspend fun storeInventory(api: RelumeApi, r: SyncResponse, apps: AppInventory.Read, file: File) {
        // ponytail: a server without App Manage answers no ack: nothing was stored there, so nothing is saved here.
        val ack = r.inventory ?: return
        check(ack.accepted) { "inventory sync: expected the full report to be accepted, got accepted=false at rev ${ack.rev}" }
        for (sha in ack.needIcons) {
            val png = checkNotNull(apps.icons[sha]) { "inventory sync: server asked for icon $sha, which no reported app has" }
            api.uploadIcon(IconUpload(sha, Base64.getEncoder().encodeToString(png)))
        }
        withContext(Dispatchers.IO) { writeSnapshot(file, InventorySnapshot(ack.rev, apps.apps)) }
    }

    private fun readSnapshot(file: File): InventorySnapshot? {
        if (!file.exists()) return null
        return try {
            SnapshotJson.decodeFromString(InventorySnapshot.serializer(), file.readText(Charsets.UTF_8))
        } catch (e: SerializationException) {
            // ponytail: the snapshot only caches what the server holds; an unreadable one (e.g. older app format) is
            // dropped and the next report is the full list, which the server always accepts.
            check(file.delete()) { "inventory snapshot ${file.path}: unreadable ($e) and cannot delete" }
            null
        }
    }

    private fun writeSnapshot(file: File, snapshot: InventorySnapshot) {
        val temp = File(file.path + ".tmp")
        FileOutputStream(temp).use { out ->
            out.write(SnapshotJson.encodeToString(InventorySnapshot.serializer(), snapshot).toByteArray(Charsets.UTF_8))
            out.fd.sync()
        }
        Files.move(temp.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }
}
