package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboard
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.InstallRunner
import kotlinx.coroutines.launch

private enum class AppFilter(val title: String, val matches: (AppFacts) -> Boolean) {
    All("All", { true }),
    NotInstalled("Not installed", { it.phone == PhoneState.NotInstalled }),
    PhoneUpdate("Phone update", { it.phone == PhoneState.Update }),
    Failed("Failed", { it.app.status.state == STATE_FAILED }),
    NeedsUrl("Needs URL", { it.lookup != null }),
}

/** Apps tab: filter chips, one card per enabled app, pull-to-refresh. Builds reach the phone only through Install and Update. */
@Composable
fun AppsScreen(model: AppModel, onOpenSettings: (Int) -> Unit, onOpenLog: (Int, Int?) -> Unit) {
    var filter by rememberSaveable { mutableIntStateOf(0) }
    var fixing by remember { mutableStateOf<Pair<AppObject, Lookup>?>(null) }
    val all = model.apps.map(model::facts)
    val visible = all.filter { AppFilter.entries[filter].matches(it) }
    val run by InstallRunner.state.collectAsState()
    val installing = run?.steps.orEmpty()
        .filter { it.step.kind == StepKind.ITEM && it.state.active }.map { it.step.refId }.toSet()

    Column(Modifier.fillMaxSize()) {
        AppBar("Apps", actions = {
            IconAction(R.drawable.ms_sync, "Check all apps for updates", model::runSchedule, IconTone.Sync)
            Avatar()
        })
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        ChipRow(AppFilter.entries.map { f -> if (f == AppFilter.All) "All ${all.size}" else "${f.title} ${all.count(f.matches)}" }, filter) { filter = it }
        PullToRefreshBox(isRefreshing = model.refreshing, onRefresh = model::refresh, modifier = Modifier.weight(1f)) {
            LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = ScreenPadding, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(visible, key = { it.app.id }) { f ->
                    AppCard(f, model, f.app.id in installing, onOpenSettings, onOpenLog) { fixing = f.app to it }
                }
                if (visible.isEmpty() && !model.refreshing) {
                    item { Txt(if (all.isEmpty()) "No apps enabled. Enable one from the Marketplace." else "Nothing in this filter.", color = muted()) }
                }
            }
        }
    }
    fixing?.let { (app, kind) ->
        val close = { fixing = null }
        when (kind) {
            Lookup.NeedsUrl -> UrlDialog(app, close) { model.setListUrl(app.id, it, close) }
            Lookup.PickOne -> PickDialog(app, close) { model.setListUrl(app.id, it, close) }
        }
    }
}

@Composable
private fun AppCard(
    f: AppFacts,
    model: AppModel,
    installing: Boolean,
    onOpenSettings: (Int) -> Unit,
    onOpenLog: (Int, Int?) -> Unit,
    onFix: (Lookup) -> Unit,
) {
    val app = f.app
    val status = app.status
    Surface(
        Modifier.fillMaxWidth().clickable { onOpenSettings(app.id) },
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
    ) {
        Column(Modifier.padding(start = 16.dp, top = 12.dp, end = 4.dp, bottom = 4.dp)) {
            Row(Modifier.padding(end = 12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
                AppTile(app.name, app.iconUrl, model.api)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Txt(app.name, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                    Txt(listOfNotNull(app.appVersion, app.bundle.name).joinToString(" · "), style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 1)
                }
                StatusPill(status.state)
            }
            status.progress?.let { p ->
                LinearProgressIndicator(
                    { p.index.toFloat() / p.count },
                    Modifier.fillMaxWidth().padding(top = 8.dp, end = 12.dp),
                    color = MaterialTheme.relume.blue.onContainer,
                    trackColor = MaterialTheme.relume.blue.container,
                )
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                val line = f.statusLine
                Txt(
                    line.orEmpty(),
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (status.state == STATE_FAILED) MaterialTheme.colorScheme.error else muted(),
                    maxLines = 2,
                )
                val job = status.jobId
                if (job != null && (f.building || status.state == STATE_FAILED)) {
                    ActionButton(if (f.building) "Live log" else "Log", { onOpenLog(job, app.id) }, kind = ButtonKind.Text)
                }
                when (f.lookup) {
                    Lookup.NeedsUrl -> ActionButton("Add URL", { onFix(Lookup.NeedsUrl) }, kind = ButtonKind.Text)
                    Lookup.PickOne -> ActionButton("Pick one", { onFix(Lookup.PickOne) }, kind = ButtonKind.Text)
                    null -> Unit
                }
                IconAction(R.drawable.ms_sync, "Check ${app.name} for updates", { model.check(app.id) }, IconTone.Sync, enabled = !f.building)
                val tonal = status.state == "update_available" || status.state == STATE_FAILED
                IconAction(
                    R.drawable.ms_build,
                    if (status.state == STATE_FAILED) "Retry ${app.name} build" else "Rebuild ${app.name}",
                    { model.rebuild(app.id) { job -> onOpenLog(job, app.id) } },
                    if (tonal) IconTone.Build else IconTone.Plain,
                    enabled = !f.building && status.state != STATE_NEEDS_URL,
                )
            }
            PhoneRow(f, installing, model)
        }
    }
}

/** Bottom row of a card, this phone: state pill on the left, Install or Update on the right. */
@Composable
private fun PhoneRow(f: AppFacts, installing: Boolean, model: AppModel) {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth().padding(end = 12.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        when {
            installing -> Pill("Installing ${f.target.orEmpty()}".trim(), r.blue.container, r.blue.onContainer, R.drawable.ms_progress_activity)
            f.phone == PhoneState.Installed -> Pill("Installed ${f.installed?.versionName.orEmpty()}".trim(), r.green.container, r.green.onContainer, R.drawable.ms_check)
            f.phone == PhoneState.Update -> Pill("Phone has ${f.installed?.versionName ?: "an older version"}", r.lavender.container, r.lavender.onContainer, R.drawable.ms_system_update)
            f.phone == PhoneState.Blocked -> Pill("Install blocked", s.errorContainer, s.onErrorContainer, R.drawable.ms_error)
            else -> Pill("Not installed", s.surfaceContainerHigh, s.onSurfaceVariant)
        }
        Spacer(Modifier.weight(1f))
        when {
            installing -> Unit
            f.installable && f.phone == PhoneState.NotInstalled ->
                ActionButton(listOfNotNull("Install", f.target).joinToString(" "), { model.beginInstall(f) }, kind = ButtonKind.Install, icon = R.drawable.ms_install_mobile)
            f.installable && f.phone == PhoneState.Update ->
                ActionButton("Update", { model.beginInstall(f) }, kind = ButtonKind.Install, icon = R.drawable.ms_system_update)
            f.building && f.phone == PhoneState.Update -> Txt("Update after build", style = MaterialTheme.typography.bodyMedium, color = muted())
        }
    }
}

/** "Add URL": paste or type the APKMirror listing; the server validates it and starts the update run. */
@Composable
private fun UrlDialog(app: AppObject, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var url by remember { mutableStateOf(app.download.apkmirrorListUrl.orEmpty()) }
    val clipboard = LocalClipboard.current
    val scope = rememberCoroutineScope()
    RDialog(onDismiss) {
        Txt("APKMirror listing for ${app.name}", style = MaterialTheme.typography.headlineSmall)
        app.download.error?.let { Txt(it, style = MaterialTheme.typography.bodyMedium, color = muted()) }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Field("APKMirror listing URL", url, { url = it }, Modifier.weight(1f), leadingIcon = R.drawable.ms_link, mono = true)
            IconAction(R.drawable.ms_content_paste, "Paste URL", {
                scope.launch { clipboard.getClipEntry()?.clipData?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.text?.let { url = it.toString().trim() } }
            })
        }
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Save", { onSave(url.trim()) }, Modifier.weight(1f), enabled = url.isNotBlank())
        }
    }
}

/** "Pick one": two or more APKMirror pages matched exactly; the user chooses (spec 5.1, no heuristic). */
@Composable
private fun PickDialog(app: AppObject, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    var chosen by remember { mutableStateOf<String?>(null) }
    RDialog(onDismiss) {
        Txt("Which APKMirror page is ${app.name}?", style = MaterialTheme.typography.headlineSmall)
        Column {
            app.download.candidates.forEach { c ->
                RadioRow(chosen == c.listUrl, onSelect = { chosen = c.listUrl }) {
                    Column(Modifier.weight(1f)) {
                        Txt(c.title, maxLines = 1)
                        Txt(c.url, style = Mono, color = muted(), maxLines = 1)
                    }
                }
            }
        }
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Use this page", { chosen?.let(onPick) }, Modifier.weight(1f), enabled = chosen != null)
        }
    }
}
