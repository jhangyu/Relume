package io.github.jhangyu.relume.inventory

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.action.ActionExecutor
import io.github.jhangyu.relume.api.ApiError
import java.io.IOException

/**
 * The App Manage inventory in the background, after every [Inventory.sync]: all user apps (sizes, icons) to the server, then
 * the App Manage actions it delivered. Kept off the regular sync so the UI and the check never wait for the full app list.
 */
class InventoryWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as RelumeApp
        val api = app.api ?: run {
            Log.w(TAG, "inventory sync skipped: not signed in to a Relume server")
            return Result.failure()
        }
        val actions = try {
            Inventory.syncApps(app, api)
        } catch (e: ApiError) {
            Log.w(TAG, "inventory sync failed: $e")
            return Result.failure()
        } catch (e: IOException) {
            Log.w(TAG, "inventory sync: ${e.javaClass.simpleName}: ${e.message}, retried with backoff")
            return Result.retry()
        }
        ActionExecutor.run(app, api, actions)
        return Result.success()
    }

    companion object {
        private const val TAG = "InventoryWorker"
        private const val WORK_NAME = "inventory"

        /** KEEP: one inventory run at a time (it owns the diff snapshot); a sync during a run leaves it be, the next sync starts another. */
        fun enqueue(context: Context) {
            val request = OneTimeWorkRequestBuilder<InventoryWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, request)
        }
    }
}
