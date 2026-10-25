package io.github.jhangyu.relume.install

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.content.pm.VersionedPackage
import android.os.Build
import androidx.core.app.PendingIntentCompat
import io.github.jhangyu.relume.api.PlanStep
import java.io.File

/** PackageInstaller sessions and uninstalls (spec 9.4). Results arrive at [InstallResultReceiver]. */
object Installer {
    private const val APK_NAME = "base.apk"

    /** Creates a session for [step]; the caller records the id before writing, so a restart never abandons it. */
    fun create(context: Context, step: PlanStep, size: Long): Int {
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(step.packageName)
            setSize(size)
            setInstallReason(PackageManager.INSTALL_REASON_USER)
            // Approved version branch 1 of 2 (spec 8.1): no prompt for updates where Android allows it.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        return context.packageManager.packageInstaller.createSession(params)
    }

    /** Copies [file] into session [sessionId] and fsyncs it. */
    fun write(context: Context, sessionId: Int, file: File) {
        context.packageManager.packageInstaller.openSession(sessionId).use { session ->
            session.openWrite(APK_NAME, 0, file.length()).use { out ->
                file.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
        }
    }

    fun commit(context: Context, sessionId: Int) {
        val intent = Intent(context, InstallResultReceiver::class.java).setAction(InstallResultReceiver.ACTION_INSTALL)
        // Mutable: the system fills in the status extras (spec 8.2, PendingIntentCompat owns the API 31 flag).
        val pending = checkNotNull(
            PendingIntentCompat.getBroadcast(context, sessionId, intent, PendingIntent.FLAG_UPDATE_CURRENT, true),
        ) { "session $sessionId: no PendingIntent for the install result" }
        context.packageManager.packageInstaller.openSession(sessionId).use { it.commit(pending.intentSender) }
    }

    fun abandon(context: Context, sessionId: Int) = context.packageManager.packageInstaller.abandonSession(sessionId)

    fun sessionExists(context: Context, sessionId: Int) = context.packageManager.packageInstaller.getSessionInfo(sessionId) != null

    /** Abandons this app's sessions that no run step references (left over after process death). */
    fun abandonOwnSessionsExcept(context: Context, keep: Set<Int>) {
        val pi = context.packageManager.packageInstaller
        pi.mySessions.map { it.sessionId }.filter { it !in keep }.forEach(pi::abandonSession)
    }

    /** System uninstall confirmation for step [stepIndex]; the result arrives as [InstallResultReceiver.ACTION_UNINSTALL]. */
    fun uninstall(context: Context, packageName: String, stepIndex: Int) {
        val intent = Intent(context, InstallResultReceiver::class.java)
            .setAction(InstallResultReceiver.ACTION_UNINSTALL)
            .putExtra(InstallResultReceiver.EXTRA_STEP, stepIndex)
        val pending = checkNotNull(
            PendingIntentCompat.getBroadcast(context, stepIndex, intent, PendingIntent.FLAG_UPDATE_CURRENT, true),
        ) { "uninstall of $packageName: no PendingIntent for the result" }
        context.packageManager.packageInstaller.uninstall(
            VersionedPackage(packageName, PackageManager.VERSION_CODE_HIGHEST),
            pending.intentSender,
        )
    }
}
