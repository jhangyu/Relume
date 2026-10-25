package io.github.jhangyu.relume.install

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.RelumeApi
import io.github.jhangyu.relume.inventory.Inventory
import io.github.jhangyu.relume.sync.Notifications
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException

/**
 * Drives the run (spec 9.1): sends due receipts, advances steps until one is committed to PackageInstaller, then
 * returns; [InstallResultReceiver] enqueues it again with the result. IO errors return `Result.retry()` and the next
 * attempt resumes the `.part` file (spec 8.7). No foreground service.
 */
class InstallWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val ctx = applicationContext
        InstallRunStore.load(ctx)
        val api = (ctx as RelumeApp).api
        if (api == null) {
            failRunnable(ctx, "not signed in to a Relume server")
            return@withContext Result.failure()
        }
        try {
            while (true) {
                sendReceipts(ctx, api)
                val run = InstallRunStore.state.value ?: return@withContext Result.success()
                // One session at a time: its result broadcast enqueues the next pass.
                if (run.steps.any { it.state in WAITING }) return@withContext Result.success()
                val index = run.nextRunnable() ?: break
                if (runStep(ctx, api, run, index)) return@withContext Result.success()
            }
            finishRun(ctx, api)
            Result.success()
        } catch (e: IOException) {
            // The allowed IO case: WorkManager backoff, the download resumes from the .part file.
            Result.retry()
        }
    }

    /** Runs step [index] up to its commit; true = committed (stop and wait for the result broadcast). */
    private fun runStep(ctx: Context, api: RelumeApi, run: RunState, index: Int): Boolean {
        val s = run.steps[index]
        val d = checkNotNull(s.step.download) { "step ${s.step.displayName}: runnable without a download" }
        if (!InstallRunner.canInstall(ctx)) {
            finish(ctx, index, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "install permission off"))
            if (run.origin == Origin.BACKGROUND) Notifications.installPermissionOff(ctx, index, s.step.displayName)
            return false
        }
        VerifyRules.checkInstalled(d, Inventory.installed(ctx, s.step.packageName))?.let {
            finish(ctx, index, StepOutcome(StepState.NEEDS_UNINSTALL, it.code, it.message))
            if (run.origin == Origin.BACKGROUND) Notifications.needsReinstall(ctx, index, s.step.displayName)
            return false
        }
        if (!move(ctx, index, setOf(StepState.PENDING, StepState.DOWNLOADING, StepState.VERIFYING), StepState.DOWNLOADING)) return false
        val apk = try {
            Downloader.download(api, d, File(ctx.filesDir, APK_DIR)) { isStopped }
        } catch (e: StepFailure) {
            finish(ctx, index, StepOutcome(StepState.FAILED, e.code, e.message))
            return false
        }
        if (!move(ctx, index, setOf(StepState.DOWNLOADING), StepState.VERIFYING)) return false
        ApkVerifier.verify(ctx, s.step, d, apk)?.let {
            finish(ctx, index, StepOutcome(StepState.FAILED, it.code, it.message))
            return false
        }
        var sessionId: Int? = null
        try {
            val id = Installer.create(ctx, s.step, apk.file.length())
            sessionId = id
            // Recorded at once: reconcileOnStart keeps referenced sessions, the result broadcast finds the step by id.
            if (!move(ctx, index, setOf(StepState.VERIFYING), StepState.VERIFYING, id)) {
                Installer.abandon(ctx, id)
                return false
            }
            Installer.write(ctx, id, apk.file)
            if (!move(ctx, index, setOf(StepState.VERIFYING), StepState.COMMITTED, id)) {
                Installer.abandon(ctx, id)
                return false
            }
            Installer.commit(ctx, id)
        } catch (e: Exception) {
            // Any session failure (IO, security, illegal state) ends the step; it must not stay "committed".
            finish(ctx, index, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "PackageInstaller session: $e"))
            sessionId?.let { Installer.abandon(ctx, it) }
            return false
        } finally {
            check(!apk.file.exists() || apk.file.delete()) { "${apk.file}: cannot delete after the session write" }
        }
        return true
    }

    private suspend fun sendReceipts(ctx: Context, api: RelumeApi) {
        val run = InstallRunStore.state.value ?: return
        run.steps.forEachIndexed { i, s ->
            if (!s.receiptDue) return@forEachIndexed
            val error = try {
                api.receipt(InstallRun.receipt(s))
                null
            } catch (e: ApiError) {
                e
            }
            InstallRunStore.update(ctx) { cur ->
                cur?.let {
                    val stale = error?.status == HTTP_CONFLICT
                    val done = it.steps[i].copy(receiptDue = false, receiptError = if (stale) null else error?.toString())
                    it.copy(steps = it.steps.toMutableList().also { l -> l[i] = done }, stalePlan = it.stalePlan || stale)
                }
            }
        }
    }

    /** No runnable step left: once nothing is committed or awaiting the user, re-sync and report a background run. */
    private suspend fun finishRun(ctx: Context, api: RelumeApi) {
        val run = InstallRunStore.state.value ?: return
        if (run.isActive || run.synced) return
        val error = try {
            Inventory.sync(ctx, api)
            null
        } catch (e: ApiError) {
            e.toString()
        }
        InstallRunStore.update(ctx) { cur -> cur?.copy(synced = true, syncError = error) }
        if (run.origin == Origin.BACKGROUND) Notifications.runFinished(ctx, run)
    }

    private fun failRunnable(ctx: Context, text: String) {
        InstallRunStore.update(ctx) { cur ->
            cur?.let { run ->
                run.steps.indices.fold(run) { acc, i ->
                    if (acc.steps[i].state.runnable) InstallRun.finish(acc, i, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, text)) else acc
                }
            }
        }
    }

    private fun finish(ctx: Context, index: Int, outcome: StepOutcome) {
        InstallRunStore.update(ctx) { cur -> cur?.let { InstallRun.finish(it, index, outcome) } }
    }

    private fun move(ctx: Context, index: Int, from: Set<StepState>, to: StepState, sessionId: Int? = null): Boolean {
        var moved = false
        InstallRunStore.update(ctx) { cur ->
            cur?.let { run -> InstallRun.move(run, index, from, to, sessionId ?: run.steps[index].sessionId)?.also { moved = true } } ?: cur
        }
        return moved
    }

    companion object {
        private const val WORK_NAME = "install-run"
        private const val APK_DIR = "apk"
        private const val HTTP_CONFLICT = 409
        private val WAITING = setOf(StepState.COMMITTED, StepState.AWAITING_USER)

        /** [replace] for a new or cancelled run; otherwise appended to the current chain (spec 9.1). */
        fun enqueue(context: Context, origin: Origin, replace: Boolean) {
            val constraints = when (origin) {
                Origin.UI -> Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
                Origin.BACKGROUND -> Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.UNMETERED)
                    .setRequiresBatteryNotLow(true)
                    .build()
            }
            val request = OneTimeWorkRequestBuilder<InstallWorker>().setConstraints(constraints).build()
            val policy = if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE
            WorkManager.getInstance(context).enqueueUniqueWork(WORK_NAME, policy, request)
        }
    }
}
