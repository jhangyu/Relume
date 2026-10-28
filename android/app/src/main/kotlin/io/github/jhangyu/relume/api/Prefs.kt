package io.github.jhangyu.relume.api

import android.content.Context

enum class CheckInterval { OFF, H1, H6, H24, DAILY }

/** Plain app settings (no secrets; the token lives in [TokenStore]). */
class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Stored as text and re-parsed, so a stored value always passed [ServerUrl.parse]. */
    var serverUrl: ServerUrl?
        get() = prefs.getString(KEY_SERVER_URL, null)?.let(ServerUrl::parse)
        set(value) = save { if (value == null) remove(KEY_SERVER_URL) else putString(KEY_SERVER_URL, value.toString()) }

    var checkInterval: CheckInterval
        get() = prefs.getString(KEY_CHECK_INTERVAL, null)?.let(CheckInterval::valueOf) ?: DEFAULT_CHECK_INTERVAL
        set(value) = save { putString(KEY_CHECK_INTERVAL, value.name) }

    /** Time of [CheckInterval.DAILY], minutes after local midnight (spec install-states 4). */
    var dailyCheckMinute: Int
        get() = prefs.getInt(KEY_DAILY_CHECK_MINUTE, DEFAULT_DAILY_CHECK_MINUTE)
        set(value) {
            require(value in 0 until MINUTES_PER_DAY) { "daily check time: expected 0..${MINUTES_PER_DAY - 1} minutes after midnight, got $value" }
            save { putInt(KEY_DAILY_CHECK_MINUTE, value) }
        }

    /** `plan.revision` of the last background install run (spec 9.5). */
    var lastBackgroundRevision: String?
        get() = prefs.getString(KEY_LAST_BACKGROUND_REVISION, null)
        set(value) = save { putString(KEY_LAST_BACKGROUND_REVISION, value) }

    private fun save(edit: android.content.SharedPreferences.Editor.() -> Unit) {
        val saved = prefs.edit().apply(edit).commit()
        check(saved) { "prefs: SharedPreferences '$PREFS' commit failed" }
    }

    private companion object {
        const val PREFS = "relume"
        const val KEY_SERVER_URL = "server_url"
        const val KEY_CHECK_INTERVAL = "check_interval"
        const val KEY_LAST_BACKGROUND_REVISION = "last_background_revision"
        const val KEY_DAILY_CHECK_MINUTE = "daily_check_minute"
        const val MINUTES_PER_DAY = 24 * 60
        /** Spec install-states 4: 03:15 until the user picks another time. */
        const val DEFAULT_DAILY_CHECK_MINUTE = 3 * 60 + 15
        /** Spec 9.5: 6 h until the user picks another interval. */
        val DEFAULT_CHECK_INTERVAL = CheckInterval.H6
    }
}
