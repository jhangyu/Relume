package io.github.jhangyu.relume.install

import android.content.Context

/**
 * Patch version Relume installed, per built APK package (spec install-states 1). Written when an install step ends
 * `installed`; the phone cannot read a patch version from an installed APK.
 */
object InstalledPatches {
    private const val PREFS = "relume-patches"

    /** Null = Relume never recorded an install of [packageName]. */
    fun get(context: Context, packageName: String): String? = prefs(context).getString(packageName, null)

    internal fun record(context: Context, packageName: String, patchVersion: String) {
        val saved = prefs(context).edit().putString(packageName, patchVersion).commit()
        check(saved) { "patch record: SharedPreferences '$PREFS' commit failed for $packageName = $patchVersion" }
    }

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
