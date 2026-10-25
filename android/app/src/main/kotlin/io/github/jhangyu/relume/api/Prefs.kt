package io.github.jhangyu.relume.api

import android.content.Context

enum class CheckInterval { OFF, H1, H6, H24 }

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
        /** Spec 9.5: 6 h until the user picks another interval. */
        val DEFAULT_CHECK_INTERVAL = CheckInterval.H6
    }
}
