package io.github.jhangyu.relume.action

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.core.content.pm.PackageInfoCompat
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.ActionResult
import io.github.jhangyu.relume.api.ActionStart
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.DeviceAction
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlayApk
import io.github.jhangyu.relume.api.RelumeApi
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.AppVisibility
import io.github.jhangyu.relume.install.InstallRunner
import io.github.jhangyu.relume.install.Installer
import io.github.jhangyu.relume.install.Origin
import io.github.jhangyu.relume.install.StartResult
import io.github.jhangyu.relume.inventory.Inventory
import io.github.jhangyu.relume.sync.Notifications
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentHashMap

/**
 * Carries out the App Manage actions a sync delivered (plan §1.4, §1.6): `build` through the scoped install plan,
 * `play_direct` streamed into one PackageInstaller session, `remove` through the system uninstall, `play_page` as a
 * notification. Every delivered action is stored first ([ActionStore]) and kept until its result is posted.
 */
object ActionExecutor {
    private const val TAG = "ActionExecutor"
    private const val HTTPS = "https"
    private const val HTTP_OK = 200
    private const val HTTP_NOT_FOUND = 404
    private const val HTTP_CONFLICT = 409
    private val runLock = Mutex()
    /** Uninstalls requested in this process; their confirm intent is still held by the system or a notification. */
    private val uninstallRequested: MutableSet<Long> = ConcurrentHashMap.newKeySet()

    /**
     * Call after every `Inventory.sync` and `Inventory.syncApps` with its [delivered] actions. Runs the new ones, settles started ones whose status
     * never arrived, posts every result held; true when it started (or queued) an install run for `build` actions.
     */
    suspend fun run(context: Context, api: RelumeApi, delivered: List<DeviceAction>): Boolean = runLock.withLock {
        withContext(Dispatchers.IO) {
            val ctx = context.applicationContext
            ActionStore.add(ctx, delivered)
            val builds = mutableListOf<Pair<StoredAction, Route.Build>>()
            for (s in ActionStore.all(ctx)) {
                if (s.result != null) continue
                when (val route = ActionRules.route(s.action)) {
                    is Route.Invalid -> ActionStore.finish(ctx, s.action.id, ActionRules.failed(ActionRules.INSTALLER_ERROR, route.reason))
                    is Route.Build -> builds += s to route
                    Route.PlayDirect -> playDirect(ctx, api, s)
                    Route.Remove -> remove(ctx, s)
                    Route.PlayPage -> playPage(ctx, s)
                }
            }
            val plan = buildPlan(ctx, api, builds)
            report(ctx, api)
            // Last: a start from the install worker replaces that worker (ExistingWorkPolicy.REPLACE), which cancels it.
            plan?.let { startBuilds(ctx, it) } ?: false
        }
    }

    /** Posts the stored results; 404/409 (the action was replaced or cancelled on the web) drop it, other failures keep it for the next run. */
    suspend fun report(context: Context, api: RelumeApi) {
        for (s in ActionStore.all(context)) {
            val r = s.result ?: continue
            val id = s.action.id
            val error = try {
                api.reportAction(id, r)
                null
            } catch (e: ApiError) {
                if (e.status == HTTP_NOT_FOUND || e.status == HTTP_CONFLICT) {
                    Log.i(TAG, "action $id result ${r.result} not taken by the server, dropped: $e")
                    null
                } else {
                    e.toString()
                }
            } catch (e: IOException) {
                "${e.javaClass.simpleName}: ${e.message}"
            }
            if (error == null) ActionStore.remove(context, setOf(id))
            else Log.w(TAG, "action $id result ${r.result} not posted, kept for the next run: $error")
        }
    }

    /** From the result receiver: posts the stored results off the main thread, then [done]. */
    fun reportAsync(context: Context, done: () -> Unit) {
        val ctx = context.applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            try {
                // Signed out: the results stay stored; the next signed-in run posts them.
                (ctx as RelumeApp).api?.let { report(ctx, it) }
            } finally {
                done()
            }
        }
    }

    /**
     * Status broadcast for action [id] (install session or uninstall). A confirmation is shown; a final status is stored
     * as the result. True = a result was stored and is to be posted.
     */
    fun onResult(context: Context, id: Long, status: Int, message: String?, confirm: Intent?): Boolean {
        val s = ActionStore.get(context, id)
        if (s == null || s.result != null) {
            Log.w(TAG, "action $id status $status: no open action with this id (stale result)")
            return false
        }
        val remove = ActionRules.route(s.action) == Route.Remove
        val result = ActionRules.result(remove, status, message)
        if (result == null) {
            val title = (if (remove) "Tap to confirm removal of " else "Tap to confirm install of ") + s.action.packageName
            show(context, id, title, checkNotNull(confirm) { "action $id: STATUS_PENDING_USER_ACTION without EXTRA_INTENT" })
            return false
        }
        Notifications.cancelAction(context, id)
        ActionStore.finish(context, id, result)
        return true
    }

    /** Some installers (vivo) never report the uninstall success; the removal itself ends an uninstalling action. */
    fun onPackageRemoved(context: Context, packageName: String): Boolean {
        val open = ActionStore.all(context).filter {
            it.result == null && it.phase == ActionPhase.UNINSTALLING && it.action.packageName == packageName
        }
        open.forEach {
            Notifications.cancelAction(context, it.action.id)
            ActionStore.finish(context, it.action.id, ActionRules.DONE)
        }
        return open.isNotEmpty()
    }

    private suspend fun playDirect(ctx: Context, api: RelumeApi, s: StoredAction) {
        val id = s.action.id
        when (s.phase) {
            ActionPhase.NEW -> Unit
            // Nothing writes outside this lock: a write seen here was cut off by process death.
            ActionPhase.WRITING -> {
                s.sessionId?.let { if (Installer.sessionExists(ctx, it)) Installer.abandon(ctx, it) }
                ActionStore.finish(ctx, id, ActionRules.failed(ActionRules.PLAY_DOWNLOAD_FAILED, "APK download interrupted (app process ended)"))
                return
            }
            ActionPhase.COMMITTED -> {
                val sid = checkNotNull(s.sessionId) { "action $id: committed without a session id" }
                if (sid in Installer.ownSessions(ctx)) return // its status broadcast is due
                val installed = Inventory.packageInfo(ctx, s.action.packageName)?.let { PackageInfoCompat.getLongVersionCode(it) }
                ActionStore.finish(ctx, id, ActionRules.sessionGone(installed, s.versionCode))
                return
            }
            ActionPhase.UNINSTALLING -> error("action $id: play_direct in phase ${s.phase}")
        }
        val start = try {
            api.startAction(id)
        } catch (e: ApiError) {
            if (ActionRules.closedByServer(e.status)) {
                Log.i(TAG, "action $id start refused, dropped: $e")
                ActionStore.remove(ctx, setOf(id))
            } else {
                ActionStore.finish(ctx, id, ActionRules.failed(ActionRules.PLAY_LOOKUP_FAILED, "action start: $e"))
            }
            return
        } catch (e: IOException) {
            ActionStore.finish(ctx, id, ActionRules.failed(ActionRules.PLAY_LOOKUP_FAILED, "action start: ${e.javaClass.simpleName}: ${e.message}"))
            return
        }
        val failure = when {
            !InstallRunner.canInstall(ctx) -> ActionRules.failed(ActionRules.INSTALLER_ERROR, "install permission off: allow Relume to install apps")
            start.apks.isEmpty() -> ActionRules.failed(ActionRules.PLAY_LOOKUP_FAILED, "action start answered no APKs")
            else -> install(ctx, s.action, start)
        }
        failure?.let { ActionStore.finish(ctx, id, it) }
    }

    /**
     * One session, each APK streamed from its URL (no file cache), then commit; null = committed. Blocking, no suspension
     * point. URLs carry Play tokens: errors name the APK and host only.
     */
    private fun install(ctx: Context, action: DeviceAction, start: ActionStart): ActionResult? {
        val sid = Installer.create(ctx, action.packageName, null)
        ActionStore.update(ctx, action.id) { it.copy(phase = ActionPhase.WRITING, sessionId = sid, versionCode = start.versionCode) }
        val code = try {
            start.apks.forEach { stream(ctx, sid, it) }
            ActionStore.update(ctx, action.id) { it.copy(phase = ActionPhase.COMMITTED) }
            Installer.commitAction(ctx, sid, action.id)
            return null
        } catch (e: IOException) {
            ActionRules.PLAY_DOWNLOAD_FAILED to e
        } catch (e: RuntimeException) {
            // Any session failure (security, illegal state) ends the action; it must not stay committed.
            ActionRules.INSTALLER_ERROR to e
        }
        Installer.abandon(ctx, sid)
        return ActionRules.failed(code.first, "${action.packageName} session: ${redact(code.second, start)}")
    }

    private fun stream(ctx: Context, sessionId: Int, apk: PlayApk) {
        val url = URL(apk.url)
        if (url.protocol != HTTPS) throw IOException("APK ${apk.name}: expected an https URL, got scheme ${url.protocol}")
        // Plain connection: the Relume bearer token never goes to Google.
        val conn = url.openConnection() as HttpURLConnection
        conn.connectTimeout = RelumeApi.CONNECT_TIMEOUT_MS
        conn.readTimeout = RelumeApi.READ_TIMEOUT_MS
        try {
            val status = conn.responseCode
            if (status != HTTP_OK) throw IOException("APK ${apk.name}: HTTP $status from ${url.host}, expected $HTTP_OK")
            conn.inputStream.use { Installer.write(ctx, sessionId, "${apk.name}.apk", it, conn.contentLengthLong) }
        } finally {
            conn.disconnect()
        }
    }

    /** Exception text without any APK URL (Play tokens). */
    private fun redact(e: Exception, start: ActionStart): String =
        start.apks.fold("${e.javaClass.simpleName}: ${e.message}") { text, apk -> text.replace(apk.url, "<${apk.name} URL>") }

    private fun remove(ctx: Context, s: StoredAction) {
        val id = s.action.id
        val pkg = s.action.packageName
        if (Inventory.packageInfo(ctx, pkg) == null) {
            // Uninstalling and gone: its success status was lost. New and absent: nothing to remove.
            ActionStore.finish(ctx, id, if (s.phase == ActionPhase.UNINSTALLING) ActionRules.DONE
                else ActionRules.failed(ActionRules.NOT_INSTALLED, "$pkg is not installed on this phone"))
            return
        }
        // Requested by an earlier process whose status never came: requested again (a fresh system prompt).
        if (!uninstallRequested.add(id)) return
        ActionStore.update(ctx, id) { it.copy(phase = ActionPhase.UNINSTALLING) }
        try {
            Installer.uninstallAction(ctx, pkg, id)
        } catch (e: SecurityException) {
            ActionStore.finish(ctx, id, ActionRules.failed(ActionRules.UNINSTALL_FAILED, "uninstall of $pkg: $e"))
        }
    }

    /** A notification opens the Play Store page; shown directly while the app is visible (background apps cannot start activities). */
    private fun playPage(ctx: Context, s: StoredAction) {
        val pkg = s.action.packageName
        val market = Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$pkg"))
        // No store app handles market:// (no Play Store): the web page.
        val open = if (market.resolveActivity(ctx.packageManager) != null) market
            else Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=$pkg"))
        val shown = if (AppVisibility.visible) {
            ctx.startActivity(open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            true
        } else {
            Notifications.playPage(ctx, s.action.id, pkg, open)
        }
        ActionStore.finish(ctx, s.action.id, if (shown) ActionRules.DONE
            else ActionRules.failed(ActionRules.ACTIVITY_START_FAILED, "notifications are off: cannot show the Play Store page of $pkg"))
    }

    private fun show(ctx: Context, id: Long, title: String, confirm: Intent) {
        if (AppVisibility.visible) {
            ctx.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        // Notifications off: the prompt is only shown while the app is visible (an install session stays until its status).
        if (!Notifications.confirmAction(ctx, id, title, confirm)) Log.w(TAG, "action $id: notifications are off, \"$title\" not shown")
    }

    /** The build actions' scoped plan; their non-install steps are stored as results. Null = nothing to start. */
    private suspend fun buildPlan(ctx: Context, api: RelumeApi, builds: List<Pair<StoredAction, Route.Build>>): BuildRun? {
        if (builds.isEmpty()) return null
        val refs = builds.map { it.second.ref }
        val plan = try {
            api.plan(
                refs.filter { it.kind == StepKind.ITEM }.map { it.id }.ifEmpty { null },
                refs.filter { it.kind == StepKind.PROJECT }.map { it.id }.ifEmpty { null },
            )
        } catch (e: ApiError) {
            builds.forEach { (s, _) -> ActionStore.finish(ctx, s.action.id, ActionRules.failed(ActionRules.INSTALLER_ERROR, "install plan: $e")) }
            return null
        } catch (e: IOException) {
            // The actions stay new: the next run asks for the plan again.
            Log.w(TAG, "install plan for ${builds.size} build actions: ${e.javaClass.simpleName}: ${e.message}; retried at the next run")
            return null
        }
        val installs = builds.mapNotNull { (s, route) ->
            val step = plan.steps.firstOrNull { it.kind == route.ref.kind && it.refId == route.ref.id }
            val outcome = ActionRules.buildOutcome(route.ref, step)
            if (outcome != null) ActionStore.finish(ctx, s.action.id, outcome)
            s.action.id.takeIf { outcome == null }
        }
        return if (installs.isEmpty()) null else BuildRun(plan, installs.toSet())
    }

    private class BuildRun(val plan: InstallPlan, val ids: Set<Long>)

    /** Starts the scoped plan; its install receipts finish the actions on the server, so they leave the store now. */
    private fun startBuilds(ctx: Context, run: BuildRun): Boolean = when (InstallRunner.start(ctx, run.plan, Origin.BACKGROUND)) {
        StartResult.Started, StartResult.Queued -> {
            ActionStore.remove(ctx, run.ids)
            true
        }
        StartResult.Corrupt -> {
            run.ids.forEach {
                ActionStore.finish(ctx, it, ActionRules.failed(ActionRules.INSTALLER_ERROR, "the install run file is corrupt: reset it in the install sheet"))
            }
            false
        }
    }
}
