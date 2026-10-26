package io.github.jhangyu.relume.ui

import kotlinx.serialization.Serializable

/** Navigation destinations: a saved back stack of these, no navigation library. Login is not a destination. */
@Serializable
sealed interface Screen {
    @Serializable data object Marketplace : Screen
    @Serializable data class MarketApp(val pkg: String) : Screen
    @Serializable data object Apps : Screen
    @Serializable data class AppSettings(val appId: Int) : Screen
    @Serializable data object Schedules : Screen
    @Serializable data object Settings : Screen
    @Serializable data class Log(val jobId: Int, val appId: Int?) : Screen
    @Serializable data object Install : Screen
}

val Screen.isRoot: Boolean get() = this is Screen.Marketplace || this is Screen.Apps || this is Screen.Schedules
