package io.github.jhangyu.relume.install

import android.content.Context
import android.content.pm.PackageManager
import io.github.jhangyu.relume.api.Download
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.inventory.ApkHashes
import io.github.jhangyu.relume.inventory.Inventory
import java.io.File
import java.security.MessageDigest

/** A verification failure: receipt code (or the needs_uninstall cause) and the text naming what differs. */
data class Mismatch(val code: String, val message: String)

/** Pure verification rules (spec 9.3); [ApkVerifier] feeds them the file and package facts. */
object VerifyRules {

    /** Lowercase hex without `:` separators, so `AB:CD` and `abcd` compare equal. */
    fun normalizeHex(hex: String) = hex.trim().replace(":", "").lowercase()

    fun hexEquals(a: String, b: String) = normalizeHex(a) == normalizeHex(b)

    /** Step 1: response headers and file hash against the plan. A missing header is a failure naming it. */
    fun checkHashes(d: Download, headers: VerifyHeaders, fileSha256: String): Mismatch? {
        val header = listOf(
            Downloader.HEADER_SHA256 to (headers.sha256 to d.sha256),
            Downloader.HEADER_SIGNER to (headers.signerSha256 to d.signerSha256),
        )
        for ((name, pair) in header) {
            val (got, want) = pair
            if (got == null) return Mismatch(ErrorCodes.HASH_MISMATCH, "response header $name missing, expected $want (stale plan)")
            if (!hexEquals(got, want)) return Mismatch(ErrorCodes.HASH_MISMATCH, "response header $name is $got, plan has $want (stale plan)")
        }
        if (!hexEquals(fileSha256, d.sha256)) {
            return Mismatch(ErrorCodes.HASH_MISMATCH, "file sha256 is $fileSha256, plan has ${d.sha256}")
        }
        return null
    }

    /** Step 2: archive package, version code and signer against the plan; null [archive] = unreadable APK. */
    fun checkArchive(step: PlanStep, d: Download, archive: InstalledPackage?): Mismatch? {
        if (archive == null) return Mismatch(ErrorCodes.SIGNER_MISMATCH_FILE, "${d.fileName}: not a readable APK")
        if (archive.packageName != step.packageName) {
            return Mismatch(ErrorCodes.SIGNER_MISMATCH_FILE, "${d.fileName}: package ${archive.packageName}, plan has ${step.packageName}")
        }
        if (archive.versionCode != d.versionCode) {
            return Mismatch(ErrorCodes.SIGNER_MISMATCH_FILE, "${d.fileName}: version code ${archive.versionCode}, plan has ${d.versionCode}")
        }
        if (!hexEquals(archive.signerSha256, d.signerSha256)) {
            return Mismatch(ErrorCodes.SIGNER_MISMATCH_FILE, "${d.fileName}: signer ${archive.signerSha256}, plan has ${d.signerSha256}")
        }
        return null
    }

    /** Step 3: an installed package with another signer or a higher version code needs an uninstall first. */
    fun checkInstalled(d: Download, installed: InstalledPackage?): Mismatch? {
        if (installed == null) return null
        if (!hexEquals(installed.signerSha256, d.signerSha256)) {
            return Mismatch(ErrorCodes.SIGNATURE_MISMATCH, "installed signer ${installed.signerSha256}, new signer ${d.signerSha256}")
        }
        if (installed.versionCode > d.versionCode) {
            return Mismatch(ErrorCodes.VERSION_DOWNGRADE, "installed version code ${installed.versionCode}, new version code ${d.versionCode}")
        }
        return null
    }

    /**
     * The phone has the planned build of [d]: same version code and signer, and the installed APK file is the planned
     * file ([installedSha256], read only when version and signer match: a patch-only rebuild keeps the version code).
     * Null [installedSha256] = the APK could not be read (the app left meanwhile): not at target.
     */
    fun atTarget(d: Download, installed: InstalledPackage?, installedSha256: () -> String?): Boolean =
        installed != null && installed.versionCode == d.versionCode && hexEquals(installed.signerSha256, d.signerSha256) &&
            installedSha256()?.let { hexEquals(it, d.sha256) } == true

    /**
     * A committed step's session vanished: it installed [d] when the phone has its version code and signer and the
     * package was replaced after the commit ([lastUpdateTime] differs from [priorUpdateTime], the package's update time
     * when the session was created; null = not installed then). No hashing: this runs on the main thread.
     */
    fun installedBySession(d: Download, installed: InstalledPackage?, lastUpdateTime: Long?, priorUpdateTime: Long?): Boolean =
        installed != null && installed.versionCode == d.versionCode && hexEquals(installed.signerSha256, d.signerSha256) &&
            (priorUpdateTime == null || lastUpdateTime != priorUpdateTime)

    fun sha256Hex(bytes: ByteArray): String = hex(MessageDigest.getInstance(SHA256).digest(bytes))

    fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }

    const val SHA256 = "SHA-256"
}

/** Android side of spec 9.3 steps 1–2. A failed check deletes the file. */
object ApkVerifier {
    private const val BUFFER = 64 * 1024

    fun verify(context: Context, step: PlanStep, d: Download, apk: DownloadedApk): Mismatch? {
        val mismatch = VerifyRules.checkHashes(d, apk.headers, fileSha256(apk.file))
            ?: VerifyRules.checkArchive(step, d, archiveFacts(context, apk.file))
        if (mismatch != null) check(apk.file.delete()) { "${apk.file}: cannot delete after failed verification" }
        return mismatch
    }

    private fun archiveFacts(context: Context, file: File): InstalledPackage? =
        context.packageManager.getPackageArchiveInfo(file.path, PackageManager.GET_SIGNING_CERTIFICATES)
            ?.let(Inventory::packageFacts)

    /**
     * sha256 of [packageName]'s installed base APK (a session writes the downloaded file unchanged as `base.apk`) through
     * the hash cache (decision 15); null = not installed. Blocking file read: call off the main thread.
     */
    fun installedSha256(context: Context, packageName: String): String? =
        Inventory.packageInfo(context, packageName)?.let { ApkHashes.of(context, it) }

    internal fun fileSha256(file: File): String {
        val md = MessageDigest.getInstance(VerifyRules.SHA256)
        file.inputStream().use { input ->
            val buf = ByteArray(BUFFER)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return VerifyRules.hex(md.digest())
    }
}
