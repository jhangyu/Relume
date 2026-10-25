package io.github.jhangyu.relume.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import io.github.jhangyu.relume.api.CheckInterval
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.concurrent.TimeUnit

/** Outcome of the last background check, shown on Settings "last check" (spec 9.5). */
@Serializable
data class CheckOutcome(val atEpochMillis: Long, val ok: Boolean, val code: String?, val message: String)

/** Unique periodic work `check` (spec 9.5). */
object CheckScheduler {
    private const val WORK_NAME = "check"
    private const val PREFS = "relume-check"
    private const val KEY_LAST = "last_check"
    private val HOURS = mapOf(CheckInterval.H1 to 1L, CheckInterval.H6 to 6L, CheckInterval.H24 to 24L)
    private val outcome = MutableStateFlow<CheckOutcome?>(null)

    /** Loaded by [load] (InstallRunner.reconcileOnStart) and updated by every check. */
    val lastCheck: Flow<CheckOutcome?> = outcome.asStateFlow()

    fun apply(context: Context, interval: CheckInterval) {
        load(context)
        val wm = WorkManager.getInstance(context)
        val hours = HOURS[interval]
        if (hours == null) {
            wm.cancelUniqueWork(WORK_NAME)
            return
        }
        val request = PeriodicWorkRequestBuilder<CheckWorker>(hours, TimeUnit.HOURS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        wm.enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request)
    }

    fun load(context: Context) {
        val text = prefs(context).getString(KEY_LAST, null) ?: return
        outcome.value = Json.decodeFromString(CheckOutcome.serializer(), text)
    }

    internal fun record(context: Context, o: CheckOutcome) {
        val saved = prefs(context).edit().putString(KEY_LAST, Json.encodeToString(CheckOutcome.serializer(), o)).commit()
        check(saved) { "check log: SharedPreferences '$PREFS' commit failed" }
        outcome.value = o
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
