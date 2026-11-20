package io.github.jhangyu.relume.install

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.IntentSender
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.content.pm.VersionedPackage
import android.os.Build
import androidx.core.app.PendingIntentCompat
import io.github.jhangyu.relume.action.ActionStore
import io.github.jhangyu.relume.api.PlanStep
import java.io.File
import java.io.InputStream

/** PackageInstaller sessions and uninstalls (spec 9.4). Results arrive at [InstallResultReceiver]. */
object Installer {
    private const val APK_NAME = "base.apk"

    /** Creates a session for [step]; the caller records the id before writing, so a restart never abandons it. */
    fun create(context: Context, step: PlanStep, size: Long): Int = create(context, step.packageName, size)

    /** A session for [packageName]; [size] null = total unknown (App Manage `play_direct` streams its APKs). */
    fun create(context: Context, packageName: String, size: Long?): Int {
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(packageName)
            size?.let { setSize(it) }
            setInstallReason(PackageManager.INSTALL_REASON_USER)
            // Approved version branch 1 of 2 (spec 8.1): no prompt for updates where Android allows it.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        return context.packageManager.packageInstaller.createSession(params)
    }

    /** Copies [file] into session [sessionId] and fsyncs it. */
    fun write(context: Context, sessionId: Int, file: File) =
        file.inputStream().use { write(context, sessionId, APK_NAME, it, file.length()) }

    /** Copies [input] into session [sessionId] as APK [name] and fsyncs it; [length] -1 = unknown (`openWrite`). */
    fun write(context: Context, sessionId: Int, name: String, input: InputStream, length: Long) {
        context.packageManager.packageInstaller.openSession(sessionId).use { session ->
            session.openWrite(name, 0, length).use { out ->
                input.copyTo(out)
                session.fsync(out)
            }
        }
    }

    fun commit(context: Context, sessionId: Int) {
        val intent = Intent(context, InstallResultReceiver::class.java).setAction(InstallResultReceiver.ACTION_INSTALL)
        context.packageManager.packageInstaller.openSession(sessionId).use {
            it.commit(resultSender(context, sessionId, intent, "session $sessionId install result"))
        }
    }

    /** Commits App Manage action [actionId]'s session; the result arrives as [InstallResultReceiver.ACTION_APP_ACTION]. */
    fun commitAction(context: Context, sessionId: Int, actionId: Long) {
        context.packageManager.packageInstaller.openSession(sessionId).use {
            it.commit(actionSender(context, actionId))
        }
    }

    /** System uninstall confirmation for App Manage action [actionId]; the result arrives as [InstallResultReceiver.ACTION_APP_ACTION]. */
    fun uninstallAction(context: Context, packageName: String, actionId: Long) =
        context.packageManager.packageInstaller.uninstall(
            VersionedPackage(packageName, PackageManager.VERSION_CODE_HIGHEST),
            actionSender(context, actionId),
        )

    private fun actionSender(context: Context, actionId: Long): IntentSender {
        val intent = Intent(context, InstallResultReceiver::class.java)
            .setAction(InstallResultReceiver.ACTION_APP_ACTION)
            .putExtra(InstallResultReceiver.EXTRA_ACTION_ID, actionId)
        // One request code per action: intents of one action compare equal, so a shared code would overwrite the extra.
        return resultSender(context, Math.toIntExact(actionId), intent, "action $actionId result")
    }

    /** Mutable: the system fills in the status extras (spec 8.2, PendingIntentCompat owns the API 31 flag). */
    private fun resultSender(context: Context, requestCode: Int, intent: Intent, what: String): IntentSender =
        checkNotNull(
            PendingIntentCompat.getBroadcast(context, requestCode, intent, PendingIntent.FLAG_UPDATE_CURRENT, true),
        ) { "$what: no PendingIntent" }.intentSender

    fun abandon(context: Context, sessionId: Int) = context.packageManager.packageInstaller.abandonSession(sessionId)

    fun sessionExists(context: Context, sessionId: Int) = context.packageManager.packageInstaller.getSessionInfo(sessionId) != null

    /** Ids of this app's sessions still open in the system: the ground truth for "an install is in progress". */
    fun ownSessions(context: Context): Set<Int> = context.packageManager.packageInstaller.mySessions.map { it.sessionId }.toSet()

    /** Abandons this app's sessions that no run step ([keep]) or App Manage action references (left over after process death). */
    fun abandonOwnSessionsExcept(context: Context, keep: Set<Int>) {
        val pi = context.packageManager.packageInstaller
        val actions = ActionStore.sessionIds(context)
        pi.mySessions.map { it.sessionId }.filter { it !in keep && it !in actions }.forEach(pi::abandonSession)
    }

    /** System uninstall confirmation for step [stepIndex]; the result arrives as [InstallResultReceiver.ACTION_UNINSTALL]. */
    fun uninstall(context: Context, revision: String, packageName: String, stepIndex: Int) {
        val intent = Intent(context, InstallResultReceiver::class.java)
            .setAction(InstallResultReceiver.ACTION_UNINSTALL)
            .putExtra(InstallResultReceiver.EXTRA_STEP, stepIndex)
            .putExtra(InstallResultReceiver.EXTRA_REVISION, revision)
            .putExtra(InstallResultReceiver.EXTRA_PACKAGE, packageName)
        context.packageManager.packageInstaller.uninstall(
            VersionedPackage(packageName, PackageManager.VERSION_CODE_HIGHEST),
            resultSender(context, stepIndex, intent, "uninstall of $packageName result"),
        )
    }
}
