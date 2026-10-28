package io.github.jhangyu.relume.install

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import androidx.core.content.IntentCompat

/** Not exported; receives PackageInstaller session and uninstall results (spec 9.4). */
class InstallResultReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        // Some installers (vivo) never report the uninstall success to the IntentSender; the removal itself is the signal.
        if (intent.action == Intent.ACTION_PACKAGE_FULLY_REMOVED) {
            val pkg = checkNotNull(intent.data?.schemeSpecificPart) { "package removed: no package in ${intent.data}" }
            InstallRunner.onPackageRemoved(context, pkg)
            return
        }
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, MISSING)
        check(status != MISSING) { "install result ${intent.action}: extra ${PackageInstaller.EXTRA_STATUS} missing" }
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
        val confirm = IntentCompat.getParcelableExtra(intent, Intent.EXTRA_INTENT, Intent::class.java)
        when (intent.action) {
            ACTION_INSTALL -> {
                val sessionId = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, MISSING)
                check(sessionId != MISSING) { "install result: extra ${PackageInstaller.EXTRA_SESSION_ID} missing" }
                InstallRunner.onSessionResult(context, sessionId, status, message, confirm)
            }
            ACTION_UNINSTALL -> {
                val step = intent.getIntExtra(EXTRA_STEP, MISSING)
                check(step != MISSING) { "uninstall result: extra $EXTRA_STEP missing" }
                val revision = checkNotNull(intent.getStringExtra(EXTRA_REVISION)) { "uninstall result: extra $EXTRA_REVISION missing" }
                val pkg = checkNotNull(intent.getStringExtra(EXTRA_PACKAGE)) { "uninstall result: extra $EXTRA_PACKAGE missing" }
                InstallRunner.onUninstallResult(context, revision, step, pkg, status, message, confirm)
            }
            else -> error("install result receiver: unexpected action ${intent.action}, expected $ACTION_INSTALL or $ACTION_UNINSTALL")
        }
    }

    companion object {
        const val ACTION_INSTALL = "io.github.jhangyu.relume.action.INSTALL_RESULT"
        const val ACTION_UNINSTALL = "io.github.jhangyu.relume.action.UNINSTALL_RESULT"
        const val EXTRA_STEP = "io.github.jhangyu.relume.extra.STEP"
        const val EXTRA_REVISION = "io.github.jhangyu.relume.extra.REVISION"
        const val EXTRA_PACKAGE = "io.github.jhangyu.relume.extra.PACKAGE"
        private const val MISSING = Int.MIN_VALUE
    }
}
