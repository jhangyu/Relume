package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.InstallRunner

private enum class AppFilter(val title: String, val matches: (AppFacts) -> Boolean) {
    All("All", { true }),
    NotInstalled("Not installed", { it.phone == PhoneState.NotInstalled }),
    PhoneUpdate("Phone update", { it.phone == PhoneState.Update }),
    Failed("Failed", { it.app.status.state == STATE_FAILED }),
    NeedsUrl("Needs URL", { it.app.status.state == STATE_NEEDS_URL }),
}

/** Apps tab: filter chips, one card per enabled app, pull-to-refresh. There is no Download button on the phone. */
@Composable
fun AppsScreen(model: AppModel, onOpenSettings: (Int) -> Unit, onOpenLog: (Int, Int?) -> Unit) {
    var filter by rememberSaveable { mutableIntStateOf(0) }
    val all = model.apps.map(model::facts)
    val visible = all.filter { AppFilter.entries[filter].matches(it) }
    val run by InstallRunner.state.collectAsState()
    val installing = run?.steps.orEmpty()
        .filter { it.step.kind == StepKind.ITEM && it.state.active }.map { it.step.refId }.toSet()

    Column(Modifier.fillMaxSize()) {
        AppBar("Apps", actions = { Avatar() })
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        ChipRow(AppFilter.entries.map { f -> if (f == AppFilter.All) "All ${all.size}" else "${f.title} ${all.count(f.matches)}" }, filter) { filter = it }
        PullToRefreshBox(isRefreshing = model.refreshing, onRefresh = model::refresh, modifier = Modifier.weight(1f)) {
            LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = ScreenPadding, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(visible, key = { it.app.id }) { f ->
                    AppCard(f, model, f.app.id in installing, onOpenSettings, onOpenLog)
                }
                if (visible.isEmpty() && !model.refreshing) {
                    item { Txt(if (all.isEmpty()) "No apps enabled. Enable one from the Marketplace." else "Nothing in this filter.", color = muted()) }
                }
            }
        }
    }
}

@Composable
private fun AppCard(f: AppFacts, model: AppModel, installing: Boolean, onOpenSettings: (Int) -> Unit, onOpenLog: (Int, Int?) -> Unit) {
    val app = f.app
    val status = app.status
    Surface(
        Modifier.fillMaxWidth().clickable { onOpenSettings(app.id) },
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
    ) {
        Column(Modifier.padding(start = 16.dp, top = 12.dp, end = 4.dp, bottom = 4.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
                AppTile(app.name, app.iconUrl, model.api)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Txt(app.name, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                    Txt(listOfNotNull(app.appVersion, app.bundle.name).joinToString(" · "), style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 1)
                    StatusPill(status.state)
                }
                IconAction(R.drawable.ms_sync, "Check ${app.name} for updates", { model.check(app.id) }, IconTone.Sync)
                IconAction(R.drawable.ms_build, "Rebuild ${app.name}", { model.rebuild(app.id) { job -> onOpenLog(job, app.id) } }, IconTone.Build)
            }
            status.progress?.let { p ->
                LinearProgressIndicator(
                    { p.index.toFloat() / p.count },
                    Modifier.fillMaxWidth().padding(top = 8.dp, end = 12.dp),
                    color = MaterialTheme.relume.blue.onContainer,
                    trackColor = MaterialTheme.relume.blue.container,
                )
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
                PhoneRow(f, installing, model)
                val job = status.jobId
                if (job != null && (f.building || status.state == STATE_FAILED)) {
                    ActionButton(if (f.building) "Live log" else "View log", { onOpenLog(job, app.id) }, kind = ButtonKind.Text)
                }
            }
        }
    }
}

@Composable
private fun PhoneRow(f: AppFacts, installing: Boolean, model: AppModel) {
    val r = MaterialTheme.relume
    when {
        installing -> Pill("Installing", r.blue.container, r.blue.onContainer, R.drawable.ms_progress_activity)
        f.installable && f.phone == PhoneState.NotInstalled -> ActionButton("Install", { model.beginInstall(f) }, kind = ButtonKind.Install, icon = R.drawable.ms_install_mobile)
        f.installable && f.phone == PhoneState.Update -> ActionButton("Update", { model.beginInstall(f) }, kind = ButtonKind.Install, icon = R.drawable.ms_system_update)
        f.phone == PhoneState.Installed -> Pill("Installed ${f.installed?.versionName ?: ""}".trim(), r.green.container, r.green.onContainer, R.drawable.ms_check)
        f.phone == PhoneState.Blocked -> Pill("Install blocked", MaterialTheme.colorScheme.errorContainer, MaterialTheme.colorScheme.onErrorContainer, R.drawable.ms_error)
        else -> Pill("Not installed", MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
