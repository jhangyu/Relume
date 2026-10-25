package io.github.jhangyu.relume.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.BuildConfig
import io.github.jhangyu.relume.api.CheckInterval
import io.github.jhangyu.relume.sync.CheckScheduler
import java.time.Instant

private val INTERVAL_LABELS = listOf(CheckInterval.OFF to "Off", CheckInterval.H1 to "1 h", CheckInterval.H6 to "6 h", CheckInterval.H24 to "24 h")

/** Frame 6. [notificationsOn] and [askNotifications] come from the root, which owns the API 33 permission request. */
@Composable
fun SettingsScreen(model: AppModel, notificationsOn: Boolean, askNotifications: () -> Unit) {
    val c = FluentTheme.colors
    val ctx = LocalContext.current
    val prefs = model.relume.prefs
    var interval by remember { mutableStateOf(prefs.checkInterval) }
    val lastCheck by CheckScheduler.lastCheck.collectAsState(initial = null)
    val device = model.device

    Column(Modifier.fillMaxSize()) {
        AppBar("Settings")
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            model.error?.let { MessageBar("Request failed", Tone.Danger, detail = it, action = "Dismiss", onAction = model::dismissError) }

            Section("Server")
            FCard {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Field("Server URL", prefs.serverUrl?.toString().orEmpty(), {}, icon = Icons.Server, mono = true, enabled = false)
                    Row(Modifier.heightIn(min = 44.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Txt("Connection", color = c.fg2)
                        val h = model.health
                        if (h != null) Chip("Reachable · server ${h.version}", c.ok) else Chip("Not checked", c.fg3)
                    }
                }
            }

            Section("Account")
            FCard {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.heightIn(min = 44.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Txt("Device token", color = c.fg2)
                        Txt(device?.let { "created ${formatTime(it.createdAt)} · revoke on server" } ?: "–", style = FluentType.monoSmall, color = c.fg3)
                    }
                    FButton("Sign out and forget token", model::logout, Modifier.fillMaxWidth(), kind = ButtonKind.DangerText)
                }
            }

            Section("Background")
            FCard {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Txt("Check for new builds", bold = true)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        INTERVAL_LABELS.forEach { (value, label) ->
                            FButton(label, {
                                interval = value
                                prefs.checkInterval = value
                                CheckScheduler.apply(ctx, value)
                                if (value != CheckInterval.OFF && !notificationsOn) askNotifications()
                            }, Modifier.weight(1f), kind = if (interval == value) ButtonKind.Primary else ButtonKind.Default, compact = true)
                        }
                    }
                    Hairline()
                    Row(Modifier.heightIn(min = 56.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Txt("Notifications", bold = true)
                            Txt(
                                if (notificationsOn) "On" else "Off · confirm prompts of background installs stay in the Apps list",
                                style = FluentType.small, color = c.fg3,
                            )
                        }
                        if (!notificationsOn) FButton("Allow", askNotifications, compact = true)
                    }
                    Hairline()
                    Txt("Last check", bold = true)
                    val last = lastCheck
                    Txt(
                        if (last == null) "never" else (if (last.ok) "ok" else "failed${last.code?.let { " $it" }.orEmpty()}") +
                            " · ${formatTime(Instant.ofEpochMilli(last.atEpochMillis).toString())} · ${last.message}",
                        style = FluentType.small, color = if (last != null && !last.ok) c.danger else c.fg3,
                    )
                }
            }

            Section("Storage")
            FCard {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Txt("Deletes builds of apps with Build off and download cache nothing needs.", style = FluentType.small, color = c.fg3)
                    model.cleanupFreed?.let { Txt("Freed ${formatMb(it.bytes)}", color = c.ok) }
                    FButton("Clean up", model::previewCleanup, Modifier.fillMaxWidth())
                }
            }

            Section("This device")
            FCard {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Fact("Name", device?.name ?: "–")
                    Fact("Model", device?.model ?: "–")
                    Fact("Android", device?.androidRelease ?: "–")
                    Fact("Last seen", formatTime(device?.lastSeenAt))
                }
            }

            Section("About")
            FCard {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Fact("App version", "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
                }
            }
        }
    }
    model.cleanupPreview?.let { p ->
        FDialog(model::dismissCleanup) {
            Txt("Clean up server storage", style = FluentType.title, bold = true)
            Txt(
                "Deletes ${formatMb(p.bytes)}: builds of ${p.artifactItems} Build-off apps and ${p.cacheFiles} cached download files. Builds are recreated on demand.",
                color = c.fg2,
            )
            DialogButtons {
                FButton("Cancel", model::dismissCleanup, Modifier.weight(1f))
                FButton("Clean up", model::runCleanup, Modifier.weight(1f), kind = ButtonKind.Danger)
            }
        }
    }
}

@Composable
private fun Section(title: String) {
    Txt(title, Modifier.padding(start = 4.dp, top = 8.dp), style = FluentType.label, color = FluentTheme.colors.fg3, bold = true)
}

@Composable
private fun Fact(key: String, value: String) {
    Row(Modifier.heightIn(min = 32.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Txt(key, color = FluentTheme.colors.fg2)
        Txt(value, style = FluentType.mono, maxLines = 1)
    }
}
