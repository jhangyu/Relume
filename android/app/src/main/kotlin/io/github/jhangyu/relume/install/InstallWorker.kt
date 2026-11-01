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
import io.github.jhangyu.relume.api.PlanStep
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
                // The step a queued run waits for ended here (e.g. a re-run after a lost confirmation failed).
                if (InstallRunner.startQueued(ctx)) return@withContext Result.success()
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

    /**
     * Runs step [index] up to its commit; true = committed (stop and wait for the result broadcast). First the phone's
     * real state (every origin, queued or late-started plans too): an app the user removed is skipped and unassigned, an
     * app already at its target ends `installed` without an install (receipt and patch record).
     */
    private suspend fun runStep(ctx: Context, api: RelumeApi, run: RunState, index: Int): Boolean {
        val s = run.steps[index]
        val d = checkNotNull(s.step.download) { "step ${s.step.displayName}: runnable without a download" }
        val prefs = (ctx as RelumeApp).prefs
        val installed = Inventory.installed(ctx, s.step.packageName)
        if (removedFromPhone(s, prefs.seenInstalled, installed != null)) {
            unassign(ctx, api, index, s.step)
            return false
        }
        // Per app: already at its target (the server lacks that install record, or a queued plan is older than the phone):
        // nothing to install; recorded as installed, so the receipt and the patch record are written once.
        if (VerifyRules.atTarget(d, installed) { ApkVerifier.installedSha256(ctx, s.step.packageName) }) {
            InstallRunner.finishStep(ctx, index, atTargetOutcome)
            InstallRunner.recordPatch(ctx, s.step)
            return false
        }
        if (!InstallRunner.canInstall(ctx)) {
            InstallRunner.finishStep(ctx, index, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "install permission off"))
            if (run.origin == Origin.BACKGROUND) Notifications.installPermissionOff(ctx, index, s.step.displayName)
            return false
        }
        VerifyRules.checkInstalled(d, installed)?.let {
            InstallRunner.finishStep(ctx, index, StepOutcome(StepState.NEEDS_UNINSTALL, it.code, it.message))
            return false
        }
        if (!move(ctx, index, setOf(StepState.PENDING, StepState.DOWNLOADING, StepState.VERIFYING), StepState.DOWNLOADING)) return false
        val apk = try {
            Downloader.download(api, d, File(ctx.filesDir, APK_DIR), { isStopped }) { bytes ->
                InstallRunStore.update(ctx) { cur -> cur?.let { InstallRun.progress(it, index, bytes) } }
            }
        } catch (e: StepFailure) {
            InstallRunner.finishStep(ctx, index, StepOutcome(StepState.FAILED, e.code, e.message))
            return false
        }
        if (!move(ctx, index, setOf(StepState.DOWNLOADING), StepState.VERIFYING)) return false
        ApkVerifier.verify(ctx, s.step, d, apk)?.let {
            InstallRunner.finishStep(ctx, index, StepOutcome(StepState.FAILED, it.code, it.message))
            return false
        }
        var sessionId: Int? = null
        try {
            val id = Installer.create(ctx, s.step, apk.file.length())
            sessionId = id
            // Recorded at once: reconcileOnStart keeps referenced sessions, the result broadcast finds the step by id. The
            // package's update time now tells a lost session's reconcile whether the phone replaced the package after it.
            val prior = Inventory.packageInfo(ctx, s.step.packageName)?.lastUpdateTime
            if (!move(ctx, index, setOf(StepState.VERIFYING), StepState.VERIFYING, id, prior)) {
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
            InstallRunner.finishStep(ctx, index, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "PackageInstaller session: $e"))
            sessionId?.let { Installer.abandon(ctx, it) }
            return false
        } finally {
            check(!apk.file.exists() || apk.file.delete()) { "${apk.file}: cannot delete after the session write" }
        }
        return true
    }

    /** The user removed [step]'s app: unassign it from this phone and forget it; the step ends `removed`. */
    private suspend fun unassign(ctx: Context, api: RelumeApi, index: Int, step: PlanStep) {
        val error = try {
            val d = api.me().device
            val (items, projects) = assignmentsWithout(d, step)
            api.putAssignments(d.id, items, projects)
            null
        } catch (e: ApiError) {
            e
        }
        if (error != null) {
            // Not installed either; the seen entry stays, so the next run tries the unassign again.
            InstallRunner.finishStep(ctx, index, StepOutcome(StepState.FAILED, ErrorCodes.INSTALLER_ERROR, "removed from the phone, unassign failed: $error"))
            return
        }
        (ctx as RelumeApp).prefs.updateSeenInstalled { it - seenKey(step.kind, step.refId) }
        InstalledPatches.forget(ctx, step.packageName)
        InstallRunner.finishStep(ctx, index, removedOutcome)
    }

    private suspend fun sendReceipts(ctx: Context, api: RelumeApi) {
        val run = InstallRunStore.state.value ?: return
        run.outbox.forEach { r ->
            val error = try {
                api.receipt(r)
                null
            } catch (e: ApiError) {
                if (e.status == HTTP_CONFLICT) null else "${r.kind} ${r.refId}: $e"
            }
            InstallRunStore.update(ctx) { cur -> cur?.let { InstallRun.outboxSent(it, r, error) } }
        }
        run.steps.forEachIndexed { i, s ->
            if (!s.receiptDue) return@forEachIndexed
            val r = InstallRun.receipt(s)
            val error = try {
                api.receipt(r)
                null
            } catch (e: ApiError) {
                e
            }
            val stale = error?.status == HTTP_CONFLICT
            InstallRunStore.update(ctx) { cur -> cur?.let { InstallRun.receiptSent(it, i, r, stale, error?.toString()) } }
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

    private fun move(ctx: Context, index: Int, from: Set<StepState>, to: StepState, sessionId: Int? = null, priorUpdateTime: Long? = null): Boolean {
        var moved = false
        InstallRunStore.update(ctx) { cur ->
            cur?.let { run ->
                val s = run.steps[index]
                InstallRun.move(run, index, from, to, sessionId ?: s.sessionId, priorUpdateTime ?: s.priorUpdateTime)?.also { moved = true }
            } ?: cur
        }
        return moved
    }

    companion object {
        private const val WORK_NAME = "install-run"
        private const val APK_DIR = "apk"
        private const val HTTP_CONFLICT = 409
        private val WAITING = setOf(StepState.COMMITTED, StepState.AWAITING_USER)

        /** A UI-triggered pass needs only a network; a background pass waits for unmetered and battery not low. */
        internal fun constraints(origin: Origin): Constraints = when (origin) {
            Origin.UI -> Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            Origin.BACKGROUND -> Constraints.Builder()
                .setRequiredNetworkType(NetworkType.UNMETERED)
                .setRequiresBatteryNotLow(true)
                .build()
        }

        /**
         * A pass of the install run is enqueued or running (also waiting for constraints or a retry backoff), so a
         * `downloading` / `verifying` step is live; false after the work ended or failed, so such a step is a stale record.
         */
        // ponytail: blocking read of WorkManager's database on the caller's thread (UI start), a few ms.
        fun pending(context: Context): Boolean =
            WorkManager.getInstance(context).getWorkInfosForUniqueWork(WORK_NAME).get().any { !it.state.isFinished }

        /** [origin] is who triggers this pass (sets [constraints]); [replace] for a new or cancelled run; otherwise appended to the current chain (spec 9.1). */
        fun enqueue(context: Context, origin: Origin, replace: Boolean) {
            val request = OneTimeWorkRequestBuilder<InstallWorker>().setConstraints(constraints(origin)).build()
            val policy = if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE
            WorkManager.getInstance(context).enqueueUniqueWork(WORK_NAME, policy, request)
        }
    }
}
