package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TimeInput
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.BuildConfig
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.CheckInterval
import io.github.jhangyu.relume.api.InvalidServerUrl
import io.github.jhangyu.relume.api.RelumeApi
import io.github.jhangyu.relume.api.ServerUrl
import io.github.jhangyu.relume.sync.CheckScheduler
import kotlinx.coroutines.launch
import java.io.IOException
import java.time.Instant

private val INTERVALS = listOf(CheckInterval.OFF, CheckInterval.H1, CheckInterval.H6, CheckInterval.H24, CheckInterval.DAILY)

private fun clock(minute: Int) = "%02d:%02d".format(minute / 60, minute % 60)

private fun intervalLabel(i: CheckInterval, dailyMinute: Int) = when (i) {
    CheckInterval.OFF -> "Off"
    CheckInterval.H1 -> "1 h"
    CheckInterval.H6 -> "6 h"
    CheckInterval.H24 -> "24 h"
    CheckInterval.DAILY -> "Daily at ${clock(dailyMinute)}"
}

/** Phone settings and account, opened from the avatar. [notificationsOn] and [askNotifications] come from the root, which owns the API 33 permission request. */
@Composable
fun SettingsScreen(model: AppModel, notificationsOn: Boolean, askNotifications: () -> Unit, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val prefs = model.relume.prefs
    var interval by remember { mutableStateOf(prefs.checkInterval) }
    var dailyMinute by remember { mutableIntStateOf(prefs.dailyCheckMinute) }
    var pickTime by remember { mutableStateOf(false) }
    var editUrl by remember { mutableStateOf(false) }
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
                ListRow("Server URL", Modifier.clickable { editUrl = true }, supporting = prefs.serverUrl?.toString().orEmpty())
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
                ChipRow(INTERVALS.map { intervalLabel(it, dailyMinute) }, INTERVALS.indexOf(interval)) { i ->
                    val value = INTERVALS[i]
                    interval = value
                    prefs.checkInterval = value
                    CheckScheduler.apply(ctx, value)
                    if (value != CheckInterval.OFF && !notificationsOn) askNotifications()
                }
                if (interval == CheckInterval.DAILY) {
                    Hairline()
                    ListRow("Check time", Modifier.clickable { pickTime = true }, supporting = "${clock(dailyMinute)} · while charging on unmetered Wi-Fi")
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
    if (editUrl) ServerUrlDialog(prefs.serverUrl, onDismiss = { editUrl = false }) { url ->
        prefs.serverUrl = url
        editUrl = false
        model.signOutLocally("Server changed to $url. Sign in again.")
    }
    if (pickTime) DailyTimeDialog(dailyMinute, onDismiss = { pickTime = false }) { m ->
        pickTime = false
        dailyMinute = m
        prefs.dailyCheckMinute = m
        CheckScheduler.apply(ctx, CheckInterval.DAILY)
    }
}

@Composable
private fun ServerUrlDialog(current: ServerUrl?, onDismiss: () -> Unit, onSaved: (ServerUrl) -> Unit) {
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf(current?.toString().orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    fun save() {
        error = null
        val url = try {
            ServerUrl.parse(text)
        } catch (e: InvalidServerUrl) {
            error = e.message
            return
        }
        scope.launch {
            busy = true
            try {
                RelumeApi(url) { null }.checkServer()
                onSaved(url)
            } catch (e: ApiError) {
                error = describe(e)
            } catch (e: IOException) {
                error = "Can't reach $url: ${describe(e)}"
            } finally {
                busy = false
            }
        }
    }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("Server URL") },
        text = {
            Field(
                "Server URL", text, { text = it }, mono = true, error = error, enabled = !busy,
                hint = "Checked with /api/health before saving. Saving signs this phone out.",
                keyboard = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
        },
        confirmButton = { ActionButton("Save", ::save, kind = ButtonKind.Text, enabled = !busy && text.isNotBlank() && text.trim() != current?.toString()) },
        dismissButton = { ActionButton("Cancel", onDismiss, kind = ButtonKind.Text, enabled = !busy) },
    )
}

/** "Daily at" time picker (spec install-states 4): 24 h text input, the check runs near this time. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DailyTimeDialog(minute: Int, onDismiss: () -> Unit, onPick: (Int) -> Unit) {
    val state = rememberTimePickerState(minute / 60, minute % 60, true)
    RDialog(onDismiss) {
        Txt("Daily check time", style = MaterialTheme.typography.headlineSmall)
        TimeInput(state)
        Txt("Android runs the check near this time, while the phone charges on an unmetered network.", color = muted())
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Save", { onPick(state.hour * 60 + state.minute) }, Modifier.weight(1f))
        }
    }
}
