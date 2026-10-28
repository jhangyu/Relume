package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
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
import io.github.jhangyu.relume.api.FollowedApp
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.InstallRunner
import kotlinx.coroutines.launch

private enum class AppFilter(val title: String, val matches: (AppFacts) -> Boolean, val followed: (CardAction?) -> Boolean) {
    All("All", { true }, { true }),
    NotInstalled("Not installed", { it.action == CardAction.Install }, { it == CardAction.Install }),
    PhoneUpdate("Phone update", { it.action.updates }, { it.updates }),
    Failed("Failed", { it.app.status.state == STATE_FAILED }, { false }),
    NeedsUrl("Needs URL", { it.lookup != null }, { false }),
}

/** The phone has an older or differently signed copy. */
private val CardAction?.updates: Boolean get() = this == CardAction.Upgrade || this == CardAction.Reinstall

/** A pending "Uninstall & reinstall" confirm. */
private class ReinstallAsk(val kind: StepKind, val id: Int, val name: String, val target: InstallTarget)

/** Apps tab: filter chips, one card per enabled app, pull-to-refresh. Builds reach the phone only through the card action. */
@Composable
fun AppsScreen(model: AppModel, onOpenSettings: (Int) -> Unit, onOpenLog: (Int, Int?) -> Unit) {
    var filter by rememberSaveable { mutableIntStateOf(0) }
    var fixing by remember { mutableStateOf<Pair<AppObject, Lookup>?>(null) }
    var reinstall by remember { mutableStateOf<ReinstallAsk?>(null) }
    val all = model.apps.map(model::facts)
    val visible = all.filter { AppFilter.entries[filter].matches(it) }
    val followed = model.followed.map { it to model.followedAction(it) }
    val visibleFollowed = followed.filter { (_, a) -> AppFilter.entries[filter].followed(a) }
    val run by InstallRunner.state.collectAsState()
    val installing = run?.steps.orEmpty()
        .filter { it.step.kind == StepKind.ITEM && it.state.active }.map { it.step.refId }.toSet()
    val installingProjects = run?.steps.orEmpty()
        .filter { it.step.kind == StepKind.PROJECT && it.state.active }.map { it.step.refId }.toSet()

    Column(Modifier.fillMaxSize()) {
        AppBar("Apps", actions = {
            IconAction(R.drawable.ms_sync, "Check all apps for updates", model::runSchedule, IconTone.Sync)
            Avatar()
        })
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        ChipRow(AppFilter.entries.map { f -> if (f == AppFilter.All) "All ${all.size + followed.size}" else "${f.title} ${all.count(f.matches) + followed.count { f.followed(it.second) }}" }, filter) { filter = it }
        PullToRefreshBox(isRefreshing = model.refreshing, onRefresh = model::refresh, modifier = Modifier.weight(1f)) {
            LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = ScreenPadding, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(visible, key = { it.app.id }) { f ->
                    AppCard(f, model, f.app.id in installing, onOpenSettings, onOpenLog, onFix = { fixing = f.app to it }, onReinstall = { reinstall = it })
                }
                items(visibleFollowed, key = { "followed-${it.first.id}" }) { (app, action) ->
                    FollowedCard(app, action, app.id in installingProjects, model) { reinstall = it }
                }
                if (visible.isEmpty() && visibleFollowed.isEmpty() && !model.refreshing) {
                    val empty = when {
                        !model.loaded -> model.loadError?.let { "Apps could not be loaded: $it. Pull down to try again." } ?: "Loading…"
                        all.isEmpty() && followed.isEmpty() -> "No apps enabled. Enable one from the Marketplace."
                        else -> "Nothing in this filter."
                    }
                    item { Txt(empty, color = muted()) }
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
    reinstall?.let { r ->
        ReinstallSheet(r.name, listOfNotNull(r.target.versionName, "signed by another key").joinToString(" · "), onDismiss = { reinstall = null }) {
            reinstall = null
            model.beginReinstall(r.kind, r.id, r.target.packageName)
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
    onReinstall: (ReinstallAsk) -> Unit,
) {
    val app = f.app
    val status = app.status
    Surface(
        Modifier.fillMaxWidth().clickable { onOpenSettings(app.id) },
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLowest,
    ) {
        // Mockup .acard: 16 dp padding, no bottom padding (the install row closes the card); trailing icon buttons pulled out 12 dp.
        Column(Modifier.padding(top = 16.dp)) {
            Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                AppTile(app.name, app.iconUrl, model.api, category = app.category)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Txt(app.name, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                    val second = app.build?.patchVersion?.let { "patch $it" } ?: app.bundle.name
                    Txt(listOfNotNull(app.appVersion, second).joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = muted(), maxLines = 1)
                }
                StatusPill(status.state)
            }
            status.progress?.fraction?.let { f ->
                LinearProgressIndicator(
                    { f },
                    Modifier.fillMaxWidth().padding(start = 16.dp, top = 12.dp, end = 16.dp),
                    color = MaterialTheme.relume.blue.onContainer,
                    trackColor = MaterialTheme.relume.blue.container,
                )
            }
            Row(
                Modifier.fillMaxWidth().padding(start = 16.dp, top = 4.dp, end = 4.dp).heightIn(min = TouchTarget),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                val line = f.statusLine
                Txt(
                    line.orEmpty(),
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall,
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
            Hairline()
            PhoneRow(
                f.action, f.target, f.installed, installing, f.building,
                onInstall = { model.beginInstall(StepKind.ITEM, app.id) },
                onReinstall = { onReinstall(ReinstallAsk(StepKind.ITEM, app.id, app.name, checkNotNull(f.target) { "reinstall of ${app.name}: expected a built APK, found none" })) },
            )
        }
    }
}

/** Bottom row of a card, this phone (spec install-states 1): state pill on the left, the one action on the right. */
@Composable
private fun PhoneRow(
    action: CardAction?,
    target: InstallTarget?,
    installed: InstalledPackage?,
    installing: Boolean,
    building: Boolean,
    onInstall: () -> Unit,
    onReinstall: () -> Unit,
) {
    // Mockup .inst: full-bleed divider above, min 56 dp, padding 8 16 dp, content centred, 8 dp gaps.
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PhonePill(installing, action, target, installed)
        Spacer(Modifier.weight(1f))
        when {
            installing || action == null || action == CardAction.Installed -> Unit
            building -> if (action == CardAction.Upgrade) Txt("Upgrade after build", style = MaterialTheme.typography.bodySmall, color = muted())
            action == CardAction.Install ->
                ActionButton(listOfNotNull("Install", target?.versionName).joinToString(" "), onInstall, kind = ButtonKind.Install, icon = R.drawable.ms_install_mobile, size = ButtonSize.Small)
            action == CardAction.Upgrade ->
                ActionButton("Upgrade", onInstall, kind = ButtonKind.Install, icon = R.drawable.ms_system_update, size = ButtonSize.Small)
            action == CardAction.Reinstall ->
                ActionButton("Uninstall & reinstall", onReinstall, kind = ButtonKind.Danger, icon = R.drawable.ms_warning, size = ButtonSize.Small)
        }
    }
}

/** The state pill of a phone row (mockup frame 1): installing, installed, older copy, other signer; none when not installed. */
@Composable
private fun PhonePill(installing: Boolean, action: CardAction?, target: InstallTarget?, installed: InstalledPackage?) {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    when {
        installing -> Pill("Installing ${target?.versionName.orEmpty()}".trim(), r.blue.container, r.blue.onContainer, R.drawable.ms_progress_activity)
        action == CardAction.Installed -> Pill("Installed ${installed?.versionName.orEmpty()}".trim(), r.green.container, r.green.onContainer, R.drawable.ms_check)
        action == CardAction.Upgrade -> Pill("Phone has ${installed?.versionName ?: "an older version"}", r.lavender.container, r.lavender.onContainer, R.drawable.ms_system_update)
        action == CardAction.Reinstall -> Pill("Different signer", s.errorContainer, s.onErrorContainer, R.drawable.ms_error)
    }
}

/** Followed-project app: the head and the same phone row as item cards. */
@Composable
private fun FollowedCard(app: FollowedApp, action: CardAction?, installing: Boolean, model: AppModel, onReinstall: (ReinstallAsk) -> Unit) {
    val target = model.followedTarget(app)
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerLowest) {
        Column(Modifier.padding(top = 16.dp)) {
            Row(Modifier.padding(horizontal = 16.dp).padding(bottom = 12.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                AppTile(app.name, app.iconUrl, model.api, category = null)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Txt(app.name, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                    Txt(listOfNotNull(app.version, "Followed project").joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = muted(), maxLines = 1)
                }
            }
            Hairline()
            PhoneRow(
                action, target, target?.let { model.installed[it.packageName] }, installing, building = false,
                onInstall = { model.beginInstall(StepKind.PROJECT, app.id) },
                onReinstall = { onReinstall(ReinstallAsk(StepKind.PROJECT, app.id, app.name, checkNotNull(target) { "reinstall of ${app.name}: expected a release file, found none" })) },
            )
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
