package io.github.jhangyu.relume.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.install.InstallRunner
import io.github.jhangyu.relume.install.InstalledPatches
import io.github.jhangyu.relume.install.VerifyRules
import io.github.jhangyu.relume.install.Origin
import io.github.jhangyu.relume.install.StartResult
import io.github.jhangyu.relume.inventory.Inventory
import java.io.IOException

/** Periodic check (spec 9.5): sync, then start a background install run for the apps not at their target. */
class CheckWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as RelumeApp
        val api = app.api ?: return fail(CODE_SIGNED_OUT, "not signed in to a Relume server")
        // A background-only process never ran MainActivity: the run record is checked against the system here too.
        InstallRunner.reconcileOnStart(app)
        val synced = try {
            Inventory.sync(app, api)
        } catch (e: ApiError) {
            return fail("HTTP ${e.status}", e.toString())
        } catch (e: IOException) {
            record(false, CODE_IO, "${e.javaClass.simpleName}: ${e.message}")
            // The allowed IO case: WorkManager backoff.
            return Result.retry()
        }
        val plan = synced.plan
        val pending = BackgroundStart.backgroundPlan(plan) { s ->
            val d = checkNotNull(s.download) { "plan step ${s.kind} ${s.refId}: install without a download" }
            VerifyRules.atTarget(d, Inventory.installed(app, s.packageName), InstalledPatches.get(app, s.packageName))
        }
        val installs = pending?.steps?.count { it.action == PlanAction.INSTALL } ?: 0
        val message = if (pending != null) {
            when (InstallRunner.start(app, pending, Origin.BACKGROUND)) {
                StartResult.Started -> "$installs apps not at their target: background run started"
                StartResult.Queued -> "$installs apps not at their target: background run queued until the current install step ends"
                StartResult.Corrupt -> return fail(CODE_CORRUPT_RUN, "$installs apps not at their target: the install run file is corrupt, reset it in the install sheet (start any install on the Apps tab)")
            }
        } else {
            "every assigned app is at its target: no background run"
        }
        record(true, null, message)
        return Result.success()
    }

    private fun fail(code: String, message: String): Result {
        record(false, code, message)
        return Result.failure()
    }

    private fun record(ok: Boolean, code: String?, message: String) =
        CheckScheduler.record(applicationContext, CheckOutcome(System.currentTimeMillis(), ok, code, message))

    private companion object {
        const val CODE_SIGNED_OUT = "SIGNED_OUT"
        const val CODE_IO = "IO"
        const val CODE_CORRUPT_RUN = "CORRUPT_RUN_FILE"
    }
}
