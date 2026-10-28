package io.github.jhangyu.relume.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.install.InstallRunner
import io.github.jhangyu.relume.install.Origin
import io.github.jhangyu.relume.install.StartResult
import io.github.jhangyu.relume.inventory.Inventory
import java.io.IOException

/** Periodic check (spec 9.5): sync, then start a background install run for a new plan revision. */
class CheckWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as RelumeApp
        val api = app.api ?: return fail(CODE_SIGNED_OUT, "not signed in to a Relume server")
        val synced = try {
            Inventory.sync(app, api)
        } catch (e: ApiError) {
            return fail("HTTP ${e.status}", e.toString())
        } catch (e: IOException) {
            record(false, CODE_IO, "${e.javaClass.simpleName}: ${e.message}")
            // The allowed IO case: WorkManager backoff.
            return Result.retry()
        }
        InstallRunner.loadState(app)
        val plan = synced.plan
        val installs = plan.steps.count { it.action == PlanAction.INSTALL }
        val message = if (BackgroundStart.shouldStart(plan, InstallRunner.state.value?.isActive == true, app.prefs.lastBackgroundRevision)) {
            when (InstallRunner.start(app, plan, Origin.BACKGROUND)) {
                StartResult.Started -> {
                    app.prefs.lastBackgroundRevision = plan.revision
                    "$installs install steps: background run started"
                }
                StartResult.Busy -> "$installs install steps: an install run is in progress"
                StartResult.Corrupt -> return fail(CODE_CORRUPT_RUN, "$installs install steps: the install run file is corrupt, reset it in the install sheet (start any install on the Apps tab)")
            }
        } else {
            "$installs install steps: no new background run"
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
