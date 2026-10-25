package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

private const val CONNECTED_BANNER_MS = 5_000L

private enum class AppFilter(val title: String, val matches: (AppFacts) -> Boolean) {
    All("All", { true }),
    Updates("Updates", { it.kind == RowKind.Update || it.kind == RowKind.NotInstalled && it.installable }),
    Building("Building", { it.kind == RowKind.Building }),
    Failed("Failed", { it.kind == RowKind.Failed || it.kind == RowKind.Blocked }),
}

/** Frame 2. Pull-to-refresh and the refresh icon reload catalog, device state and plan. */
@Composable
fun AppsScreen(
    model: AppModel,
    onOpenDetail: (Int) -> Unit,
    onOpenLog: (jobId: Int, itemId: Int) -> Unit,
    onInstall: (AppFacts) -> Unit,
) {
    val c = FluentTheme.colors
    var filter by rememberSaveable { mutableIntStateOf(0) }
    var searching by rememberSaveable { mutableStateOf(false) }
    var query by rememberSaveable { mutableStateOf("") }
    val all = model.items.map(model::facts)
    val visible = all
        .filter { AppFilter.entries[filter].matches(it) }
        .filter { query.isBlank() || it.item.label.contains(query.trim(), ignoreCase = true) || it.item.packageName.contains(query.trim(), ignoreCase = true) }

    Column(Modifier.fillMaxSize()) {
        AppBar("Apps", actions = {
            IconButton(Icons.Search, "Search apps", { searching = !searching; if (!searching) query = "" })
            IconButton(Icons.Refresh, "Refresh", model::refresh)
        })
        if (searching) Field("", query, { query = it }, Modifier.padding(horizontal = ScreenPadding), icon = Icons.Search)
        val host = model.relume.prefs.serverUrl?.host
        var showConnected by remember(host) { mutableStateOf(true) }
        LaunchedEffect(host) {
            delay(CONNECTED_BANNER_MS)
            showConnected = false
        }
        if (host != null && showConnected && model.error == null) {
            MessageBar("Connected to $host", Tone.Ok, Modifier.padding(horizontal = ScreenPadding))
        }
        model.error?.let { MessageBar("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        Pivot(AppFilter.entries.map { f -> if (f == AppFilter.All) "All ${all.size}" else "${f.title} ${all.count(f.matches)}" }, filter) { filter = it }
        PullToRefreshBox(isRefreshing = model.refreshing, onRefresh = model::refresh, modifier = Modifier.weight(1f)) {
            LazyColumn(Modifier.fillMaxSize(), contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = ScreenPadding, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(visible, key = { it.item.id }) { f ->
                    AppCard(f, model, onOpenDetail, onOpenLog, onInstall)
                }
                if (visible.isEmpty() && !model.refreshing) {
                    item { Txt(if (all.isEmpty()) "No apps in the catalog." else "Nothing in this tab.", color = c.fg3) }
                }
            }
        }
    }
}

@Composable
private fun AppCard(f: AppFacts, model: AppModel, onOpenDetail: (Int) -> Unit, onOpenLog: (Int, Int) -> Unit, onInstall: (AppFacts) -> Unit) {
    val c = FluentTheme.colors
    val item = f.item
    FCard(Modifier.clickable { onOpenDetail(item.id) }) {
        Column(Modifier.padding(12.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
                AppTile(item.label)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Txt(item.label, bold = true, maxLines = 1)
                    VersionLine(f)
                    Chip(f.kind.label, f.kind.color())
                }
                RowAction(f, onOpenLog, onInstall)
            }
            Hairline(Modifier.padding(top = 6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                ToggleLabel("Build", item.enabled, true) { model.setSwitches(item.id, enabled = it, autoUpdate = null) }
                ToggleLabel("Update", item.autoUpdate, true) { model.setSwitches(item.id, enabled = null, autoUpdate = it) }
            }
        }
    }
}

@Composable
internal fun VersionLine(f: AppFacts) {
    val c = FluentTheme.colors
    val installedName = f.installed?.versionName
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (f.installed == null) Txt("not installed", style = FluentType.mono, color = c.fg3) else Txt(installedName ?: f.installed.versionCode.toString(), style = FluentType.mono)
        val target = f.target
        when {
            target == null && f.kind != RowKind.UpToDate -> {
                Txt("→", color = c.fg3)
                Txt("no build", style = FluentType.mono, color = c.danger)
            }
            target != null && target != installedName -> {
                Txt("→", color = c.fg3)
                Txt(target, style = FluentType.mono, color = if (f.installed != null) c.accent else c.fg)
            }
        }
    }
}

@Composable
private fun RowAction(f: AppFacts, onOpenLog: (Int, Int) -> Unit, onInstall: (AppFacts) -> Unit) {
    val item = f.item
    when {
        f.kind == RowKind.Building -> FButton("Live log", { onOpenLog(item.activeJob!!.jobId, item.id) }, compact = true)
        f.kind == RowKind.Failed -> FButton("View log", { onOpenLog(item.lastBuild!!.jobId, item.id) }, compact = true)
        f.installable -> FButton("Install", { onInstall(f) }, compact = true, kind = if (f.kind == RowKind.Update) ButtonKind.Primary else ButtonKind.Default)
    }
}
