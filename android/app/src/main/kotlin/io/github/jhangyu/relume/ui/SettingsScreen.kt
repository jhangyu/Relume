package io.github.jhangyu.relume.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.BuildConfig
import io.github.jhangyu.relume.api.CheckInterval
import io.github.jhangyu.relume.sync.CheckScheduler
import java.time.Instant

private val INTERVAL_LABELS = listOf(CheckInterval.OFF to "Off", CheckInterval.H1 to "1 h", CheckInterval.H6 to "6 h", CheckInterval.H24 to "24 h")

/** Phone settings and account, opened from the avatar. [notificationsOn] and [askNotifications] come from the root, which owns the API 33 permission request. */
@Composable
fun SettingsScreen(model: AppModel, notificationsOn: Boolean, askNotifications: () -> Unit, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val prefs = model.relume.prefs
    var interval by remember { mutableStateOf(prefs.checkInterval) }
    val lastCheck by CheckScheduler.lastCheck.collectAsState(initial = null)
    val device = model.device

    Column(Modifier.fillMaxSize()) {
        AppBar("Settings", onBack = onBack)
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            model.error?.let { Banner("Request failed", Tone.Danger, detail = it, action = "Dismiss", onAction = model::dismissError) }

            SectionHeader("Server")
            InsetGroup {
                ListRow("Server URL", supporting = prefs.serverUrl?.toString().orEmpty())
                Hairline()
                val h = model.health
                ListRow(
                    "Connection",
                    trailing = {
                        if (h != null) Pill("Reachable · server ${h.version}", MaterialTheme.relume.green.container, MaterialTheme.relume.green.onContainer)
                        else Pill("Not checked", MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurfaceVariant)
                    },
                )
            }

            SectionHeader("Account")
            InsetGroup {
                ListRow("Device token", supporting = device?.let { "created ${formatTime(it.createdAt)} · revoke on server" } ?: "–")
                Hairline()
                ActionButton("Sign out and forget token", model::logout, Modifier.fillMaxWidth().padding(8.dp), kind = ButtonKind.Text)
            }

            SectionHeader("Background")
            InsetGroup {
                Txt("Check for new builds", Modifier.padding(start = 16.dp, top = 12.dp))
                ChipRow(INTERVAL_LABELS.map { it.second }, INTERVAL_LABELS.indexOfFirst { it.first == interval }) { i ->
                    val value = INTERVAL_LABELS[i].first
                    interval = value
                    prefs.checkInterval = value
                    CheckScheduler.apply(ctx, value)
                    if (value != CheckInterval.OFF && !notificationsOn) askNotifications()
                }
                Hairline()
                ListRow(
                    "Notifications",
                    supporting = if (notificationsOn) "On" else "Off · confirm prompts of background installs stay in the Apps list",
                    trailing = { if (!notificationsOn) ActionButton("Allow", askNotifications, kind = ButtonKind.Outlined) },
                )
                Hairline()
                val last = lastCheck
                ListRow(
                    "Last check",
                    supporting = if (last == null) "never" else (if (last.ok) "ok" else "failed${last.code?.let { " $it" }.orEmpty()}") +
                        " · ${formatTime(Instant.ofEpochMilli(last.atEpochMillis).toString())} · ${last.message}",
                )
            }

            SectionHeader("Storage")
            InsetGroup {
                ListRow("Clean up", supporting = "Deletes builds of apps that are not enabled and download cache nothing needs.")
                model.cleanupFreed?.let { Txt("Freed ${formatMb(it.bytes)}", Modifier.padding(horizontal = 16.dp), color = MaterialTheme.relume.green.onContainer) }
                ActionButton("Clean up", model::previewCleanup, Modifier.fillMaxWidth().padding(8.dp), kind = ButtonKind.Outlined)
            }

            SectionHeader("This device")
            InsetGroup {
                ListRow("Name", supporting = device?.name ?: "–")
                ListRow("Model", supporting = device?.model ?: "–")
                ListRow("Android", supporting = device?.androidRelease ?: "–")
                ListRow("Last seen", supporting = formatTime(device?.lastSeenAt))
            }

            SectionHeader("About")
            InsetGroup { ListRow("App version", supporting = "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})") }
        }
    }
    model.cleanupPreview?.let { p ->
        RDialog(model::dismissCleanup) {
            Txt("Clean up server storage", style = MaterialTheme.typography.headlineSmall)
            Txt(
                "Deletes ${formatMb(p.bytes)}: builds of ${p.artifactItems} apps that are not enabled and ${p.cacheFiles} cached download files. Builds are recreated on demand.",
                color = muted(),
            )
            DialogButtons {
                ActionButton("Cancel", model::dismissCleanup, Modifier.weight(1f), kind = ButtonKind.Text)
                ActionButton("Clean up", model::runCleanup, Modifier.weight(1f), kind = ButtonKind.Danger)
            }
        }
    }
}
