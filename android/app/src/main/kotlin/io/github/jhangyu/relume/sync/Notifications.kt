package io.github.jhangyu.relume.sync

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.PendingIntentCompat
import io.github.jhangyu.relume.install.RunState
import io.github.jhangyu.relume.install.StepState

/** One channel `install`: confirm prompts, run results, needs-reinstall and permission notices (spec 9.5). */
object Notifications {
    const val CHANNEL_INSTALL = "install"
    private const val CHANNEL_NAME = "Installs"
    private const val ID_STEP_BASE = 1000
    private const val ID_RUN_FINISHED = 1
    private const val REQUEST_OPEN_APP = 0
    private val ICON = android.R.drawable.stat_sys_download_done

    fun ensureChannels(context: Context) {
        NotificationManagerCompat.from(context).createNotificationChannel(
            NotificationChannelCompat.Builder(CHANNEL_INSTALL, NotificationManagerCompat.IMPORTANCE_HIGH)
                .setName(CHANNEL_NAME)
                .build(),
        )
    }

    /** Content intent wraps the system confirm intent (`EXTRA_INTENT`), spec 9.4. */
    fun confirmInstall(context: Context, stepIndex: Int, name: String, confirm: Intent) {
        val tap = PendingIntentCompat.getActivity(context, ID_STEP_BASE + stepIndex, confirm, PendingIntent.FLAG_UPDATE_CURRENT, false)
        post(context, ID_STEP_BASE + stepIndex, "Tap to confirm install of $name", null, tap)
    }

    fun needsReinstall(context: Context, stepIndex: Int, name: String) =
        post(context, ID_STEP_BASE + stepIndex, "Open Relume: $name needs a reinstall", null, openApp(context))

    fun installPermissionOff(context: Context, stepIndex: Int, name: String) =
        post(context, ID_STEP_BASE + stepIndex, "Relume cannot install $name", "Install permission off: allow Relume to install apps", openApp(context))

    fun runFinished(context: Context, run: RunState) {
        val installed = run.steps.count { it.state == StepState.INSTALLED }
        val failed = run.steps.count { it.state != StepState.INSTALLED && it.state != StepState.UP_TO_DATE }
        post(context, ID_RUN_FINISHED, "Relume: $installed installed, $failed not installed", null, openApp(context))
    }

    fun cancelStep(context: Context, stepIndex: Int) = NotificationManagerCompat.from(context).cancel(ID_STEP_BASE + stepIndex)

    private fun openApp(context: Context): PendingIntent? {
        val launch = checkNotNull(context.packageManager.getLaunchIntentForPackage(context.packageName)) {
            "package ${context.packageName}: no launcher activity"
        }
        return PendingIntentCompat.getActivity(context, REQUEST_OPEN_APP, launch, PendingIntent.FLAG_UPDATE_CURRENT, false)
    }

    // MissingPermission: areNotificationsEnabled() is false when POST_NOTIFICATIONS is denied on API 33+; a permission
    // check would need a version branch (spec 8.1). Not posted = the Apps screen lists awaiting steps (spec 9.5).
    @SuppressLint("MissingPermission")
    private fun post(context: Context, id: Int, title: String, text: String?, tap: PendingIntent?) {
        val nm = NotificationManagerCompat.from(context)
        if (!nm.areNotificationsEnabled()) return
        val n = NotificationCompat.Builder(context, CHANNEL_INSTALL)
            .setSmallIcon(ICON)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        nm.notify(id, n)
    }
}
