package io.github.jhangyu.relume.ui

import kotlinx.serialization.Serializable

/** Navigation destinations (spec 10): a saved back stack of these, no navigation library. Login is not a destination. */
@Serializable
sealed interface Screen {
    @Serializable data object Apps : Screen
    @Serializable data object Builds : Screen
    @Serializable data object Settings : Screen
    @Serializable data class Detail(val itemId: Int) : Screen
    @Serializable data class Log(val jobId: Int, val itemId: Int?) : Screen
    @Serializable data object Install : Screen
}

val Screen.isRoot: Boolean get() = this is Screen.Apps || this is Screen.Builds || this is Screen.Settings
