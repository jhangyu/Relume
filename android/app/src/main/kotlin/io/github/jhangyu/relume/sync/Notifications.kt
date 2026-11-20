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
    /** App Manage notifications use the tag `action-<id>` with this id; their PendingIntents use request codes below 0. */
    private const val ID_ACTION = 0
    private const val REQUEST_ACTION_BASE = Int.MIN_VALUE
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
        val failed = run.steps.count { it.state != StepState.INSTALLED && it.state != StepState.UP_TO_DATE && it.state != StepState.REMOVED }
        post(context, ID_RUN_FINISHED, "Relume: $installed installed, $failed not installed", null, openApp(context))
    }

    fun cancelStep(context: Context, stepIndex: Int) = NotificationManagerCompat.from(context).cancel(ID_STEP_BASE + stepIndex)

    /** System confirmation of an App Manage install or uninstall ([title] names it); false = notifications are off. */
    fun confirmAction(context: Context, actionId: Long, title: String, confirm: Intent): Boolean =
        post(context, ID_ACTION, title, null, actionIntent(context, actionId, confirm), actionTag(actionId))

    /** App Manage `play_page`: tapping opens [open] (the Play Store page); false = notifications are off. */
    fun playPage(context: Context, actionId: Long, packageName: String, open: Intent): Boolean =
        post(context, ID_ACTION, "Open the Play Store page of $packageName", "Requested on the Relume App Manage page",
            actionIntent(context, actionId, open), actionTag(actionId))

    fun cancelAction(context: Context, actionId: Long) = NotificationManagerCompat.from(context).cancel(actionTag(actionId), ID_ACTION)

    private fun actionTag(actionId: Long) = "action-$actionId"

    private fun actionIntent(context: Context, actionId: Long, intent: Intent): PendingIntent? =
        PendingIntentCompat.getActivity(context, REQUEST_ACTION_BASE + Math.toIntExact(actionId), intent, PendingIntent.FLAG_UPDATE_CURRENT, false)

    private fun openApp(context: Context): PendingIntent? {
        val launch = checkNotNull(context.packageManager.getLaunchIntentForPackage(context.packageName)) {
            "package ${context.packageName}: no launcher activity"
        }
        return PendingIntentCompat.getActivity(context, REQUEST_OPEN_APP, launch, PendingIntent.FLAG_UPDATE_CURRENT, false)
    }

    // MissingPermission: areNotificationsEnabled() is false when POST_NOTIFICATIONS is denied on API 33+; a permission
    // check would need a version branch (spec 8.1). Not posted = the Apps screen lists awaiting steps (spec 9.5);
    // App Manage callers read the false return.
    @SuppressLint("MissingPermission")
    private fun post(context: Context, id: Int, title: String, text: String?, tap: PendingIntent?, tag: String? = null): Boolean {
        val nm = NotificationManagerCompat.from(context)
        if (!nm.areNotificationsEnabled()) return false
        val n = NotificationCompat.Builder(context, CHANNEL_INSTALL)
            .setSmallIcon(ICON)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        nm.notify(tag, id, n)
        return true
    }
}
