package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.layout.widthIn
import io.github.jhangyu.relume.api.Artifact
import java.time.Duration
import java.time.Instant
import java.util.Locale

private const val BYTES_PER_MB = 1024.0 * 1024.0
private const val MINUTES_PER_HOUR = 60
private const val HOURS_PER_DAY = 24

internal fun formatMb(bytes: Long): String = String.format(Locale.ENGLISH, "%.1f MB", bytes / BYTES_PER_MB)

internal fun ago(iso: String?): String {
    if (iso == null) return "never"
    val minutes = Duration.between(Instant.parse(iso), Instant.now()).toMinutes()
    return when {
        minutes < 1 -> "just now"
        minutes < MINUTES_PER_HOUR -> "$minutes min ago"
        minutes < MINUTES_PER_HOUR * HOURS_PER_DAY -> "${minutes / MINUTES_PER_HOUR} h ago"
        else -> "${minutes / (MINUTES_PER_HOUR * HOURS_PER_DAY)} d ago"
    }
}

/** Frame 3. Item facts come from the model's catalog; artifacts are loaded here and reloaded after each finished build. */
@Composable
fun DetailScreen(
    model: AppModel,
    itemId: Int,
    onBack: () -> Unit,
    onOpenLog: (jobId: Int, itemId: Int) -> Unit,
    onInstall: (AppFacts) -> Unit,
) {
    val c = FluentTheme.colors
    val item = model.item(itemId)
    var artifacts by remember(itemId) { mutableStateOf<List<Artifact>>(emptyList()) }
    LaunchedEffect(itemId, model.buildsVersion) { model.loadArtifacts(itemId) { artifacts = it } }

    Column(Modifier.fillMaxSize()) {
        AppBar(item?.label ?: "App", onBack = onBack)
        if (item == null) {
            Txt("This app is no longer in the catalog.", Modifier.padding(ScreenPadding), color = c.fg3)
            return@Column
        }
        val f = model.facts(item)
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            model.error?.let { MessageBar("Request failed", Tone.Danger, detail = it, action = "Dismiss", onAction = model::dismissError) }
            Row(Modifier.padding(vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
                AppTile(item.label, item.iconUrl, model.api, 56.dp)
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Txt(item.packageName, style = FluentType.mono.copy(fontSize = 12.sp), maxLines = 1)
                    Chip(f.kind.label, f.kind.color())
                }
            }
            FCard {
                Column(Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
                    Txt("Versions", style = FluentType.label, bold = true, modifier = Modifier.padding(vertical = 12.dp))
                    KeyValue("On this phone", f.installed?.versionName ?: f.installed?.versionCode?.toString() ?: "not installed", null)
                    KeyValue("Latest build", item.lastSuccess?.appVersion ?: "no build", artifacts.firstOrNull()?.abi, highlight = f.kind == RowKind.Update)
                    KeyValue("Upstream", item.upstream.latestTag ?: "unknown", "checked ${ago(item.upstream.checkedAt)}")
                    item.upstream.checkError?.let { Txt("Upstream check failed: $it", style = FluentType.small, color = c.danger, modifier = Modifier.padding(bottom = 8.dp)) }
                }
            }
            FCard {
                Column(Modifier.padding(horizontal = 16.dp)) {
                    SwitchRow("Build", "Rebuild when upstream changes", item.enabled) { model.setSwitches(item.id, enabled = it, autoUpdate = null) }
                    Hairline()
                    SwitchRow("Update", "Offer installs on this phone", item.autoUpdate) { model.setSwitches(item.id, enabled = null, autoUpdate = it) }
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (f.kind == RowKind.Building) {
                    FButton("Live log", { onOpenLog(item.activeJob!!.jobId, item.id) }, Modifier.fillMaxWidth(), kind = ButtonKind.Primary)
                } else {
                    FButton(
                        "Download & install", { onInstall(f) }, Modifier.fillMaxWidth(), kind = ButtonKind.Primary,
                        enabled = f.installable,
                        subtitle = f.step?.download?.let { "${formatMb(it.size)} · sha256 and signer checked" } ?: f.step?.reason,
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FButton("Check upstream", { model.checkUpstream(item.id) }, Modifier.weight(1f), icon = Icons.Refresh)
                    FButton("Build now", { model.build(item.id) { onOpenLog(it, item.id) } }, Modifier.weight(1f), icon = Icons.Play, enabled = f.kind != RowKind.Building && item.enabled)
                }
            }
            RecentBuilds(f, artifacts, onOpenLog)
        }
    }
}

@Composable
private fun KeyValue(key: String, value: String, note: String?, highlight: Boolean = false) {
    val c = FluentTheme.colors
    Hairline()
    Row(Modifier.heightIn(min = 38.dp).padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.Bottom) {
        Txt(key, Modifier.weight(1f), color = c.fg2)
        Txt(value, style = FluentType.mono, color = if (highlight) c.accent else c.fg, bold = true)
        if (note != null) Txt(note, Modifier.widthIn(min = 100.dp), style = FluentType.small, color = c.fg3)
    }
}

@Composable
private fun RecentBuilds(f: AppFacts, artifacts: List<Artifact>, onOpenLog: (Int, Int) -> Unit) {
    val c = FluentTheme.colors
    FCard {
        Column {
            Txt("Recent builds", style = FluentType.label, bold = true, modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 4.dp))
            val failed = f.item.lastBuild?.takeIf { it.status == "failed" }
            if (failed != null) {
                BuildRow(failed.jobId, "failed", failed.reason ?: "", "", RowKind.Failed.color()) { onOpenLog(failed.jobId, f.item.id) }
            }
            artifacts.distinctBy { it.jobId }.take(RECENT_BUILDS).forEach { a ->
                BuildRow(a.jobId, a.versionName ?: a.fileName, formatTime(a.createdAt), "Built", c.ok) { onOpenLog(a.jobId, f.item.id) }
            }
            if (failed == null && artifacts.isEmpty()) Txt("No builds yet.", Modifier.padding(16.dp), color = c.fg3)
        }
    }
}

private const val RECENT_BUILDS = 5

@Composable
private fun BuildRow(jobId: Int, title: String, sub: String, chip: String, color: androidx.compose.ui.graphics.Color, onClick: () -> Unit) {
    val c = FluentTheme.colors
    Hairline()
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp).clickable(onClick = onClick).padding(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Txt("#$jobId", style = FluentType.mono)
        Column(Modifier.weight(1f)) {
            Txt(title, style = FluentType.mono, bold = true, maxLines = 1)
            if (sub.isNotEmpty()) Txt(sub, style = FluentType.small, color = c.fg3, maxLines = 1)
        }
        Chip(chip.ifEmpty { "Failed" }, color)
    }
}
