package io.github.jhangyu.relume.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.annotation.DrawableRes
import io.github.jhangyu.relume.R
import androidx.core.app.NotificationManagerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.jhangyu.relume.install.InstallRunner
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

private val StackSaver = Saver<List<Screen>, String>(
    save = { Json.encodeToString(ListSerializer(Screen.serializer()), it) },
    restore = { Json.decodeFromString(ListSerializer(Screen.serializer()), it) },
)

private class Tab(val label: String, @DrawableRes val icon: Int, @DrawableRes val selectedIcon: Int, val screen: Screen)

private val TABS = listOf(
    Tab("Marketplace", R.drawable.ms_storefront, R.drawable.ms_storefront_fill1, Screen.Marketplace),
    Tab("Apps", R.drawable.ms_apps, R.drawable.ms_apps, Screen.Apps),
    Tab("Schedules", R.drawable.ms_schedule, R.drawable.ms_schedule_fill1, Screen.Schedules),
)

/** Navigation: a saved back stack of [Screen]s and a back handler. Tabs are stack roots, Login replaces everything. */
@Composable
fun RelumeRoot() {
    RelumeTheme {
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface).windowInsetsPadding(WindowInsets.systemBars)) {
            RootContent()
        }
    }
}

@Composable
private fun ColumnScope.RootContent() {
    val model: AppModel = viewModel()
    val ctx = LocalContext.current
    var stack by rememberSaveable(stateSaver = StackSaver) { mutableStateOf<List<Screen>>(listOf(Screen.Apps)) }
    var notificationsOn by remember { mutableStateOf(NotificationManagerCompat.from(ctx).areNotificationsEnabled()) }
    val run by InstallRunner.state.collectAsState()

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        notificationsOn = NotificationManagerCompat.from(ctx).areNotificationsEnabled()
    }
    // Spec 8.1 branch 2: the only API-level check in ui/.
    val askNotifications = {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) permission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    LifecycleEventEffect(Lifecycle.Event.ON_START) { model.refresh() }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        notificationsOn = NotificationManagerCompat.from(ctx).areNotificationsEnabled()
        // Back from system settings: a granted install permission closes the "allow installs" prompt.
        if (model.prompt == AppModel.InstallPrompt.NeedPermission && InstallRunner.canInstall(ctx)) model.dismissPrompt()
    }
    LaunchedEffect(model.runStarted) {
        if (model.runStarted > 0 && stack.last() != Screen.Install) stack = stack + Screen.Install
    }
    LaunchedEffect(run?.synced) {
        if (run?.synced == true) model.refresh()
    }
    BackHandler(enabled = stack.size > 1) { stack = stack.dropLast(1) }

    if (model.api == null) {
        LoginScreen(model.relume, model.notice) {
            model.signedIn()
            io.github.jhangyu.relume.sync.CheckScheduler.apply(ctx, model.relume.prefs.checkInterval)
            askNotifications()
        }
        return
    }
    val current = stack.last()
    val account = Account {
        if (stack.last() != Screen.Settings) stack = stack + Screen.Settings
    }
    val back = { stack = stack.dropLast(1) }
    val openLog = { job: Int, app: Int? -> stack = stack + Screen.Log(job, app) }
    CompositionLocalProvider(LocalAccount provides account) {
        Column(Modifier.weight(1f)) {
            when (current) {
                Screen.Marketplace -> MarketplaceScreen(
                    model.market,
                    onOpen = { stack = stack + Screen.MarketApp(it) },
                    onAddSource = model.market::openAddSource,
                )
                is Screen.MarketApp -> MarketAppScreen(
                    model.market, current.pkg, onBack = back,
                    onEnabled = { stack = listOf(Screen.Apps, Screen.AppSettings(it)) },
                )
                Screen.Apps -> AppsScreen(model, onOpenSettings = { stack = stack + Screen.AppSettings(it) }, onOpenLog = openLog)
                is Screen.AppSettings -> AppSettingsScreen(model, current.appId, onBack = back, onOpenLog = openLog)
                Screen.Schedules -> SchedulesScreen(model, onOpenLog = openLog)
                Screen.Settings -> SettingsScreen(model, notificationsOn, askNotifications, onBack = back)
                is Screen.Log -> model.api?.let { api ->
                    val app = current.appId?.let(model::app)
                    LogScreen(api, current.jobId, app?.status?.takeIf { it.jobId == current.jobId }?.progress, app?.name, back, model::buildEnded)
                }
                Screen.Install -> InstallScreen(run, model, onClose = back)
            }
        }
    }
    AddSourceDialog(model.market)
    if (current.isRoot) {
        NavBar(current, enabledApps = model.apps.size) { stack = listOf(it) }
    }
    InstallPrompts(model)
}

/**
 * MD3 navigation bar; each tab has its own indicator colour (Marketplace amber, Apps blue, Schedules teal), the
 * selected label is bold and the Apps icon carries the enabled-app count as an error badge.
 */
@Composable
private fun NavBar(current: Screen, enabledApps: Int, onSelect: (Screen) -> Unit) {
    val r = MaterialTheme.relume
    val indicators = listOf(r.amber.container, r.blue.container, r.teal.container)
    val glyphs = listOf(r.amber.onContainer, r.blue.onContainer, r.teal.onContainer)
    NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainer) {
        TABS.forEachIndexed { i, tab ->
            val selected = tab.screen == current
            NavigationBarItem(
                selected = selected,
                onClick = { onSelect(tab.screen) },
                icon = {
                    val glyph = @Composable { Icon(painterResource(if (selected) tab.selectedIcon else tab.icon), contentDescription = null) }
                    if (tab.screen == Screen.Apps && enabledApps > 0) {
                        BadgedBox(badge = { Badge { Text("$enabledApps", Modifier.semantics { contentDescription = "$enabledApps enabled" }) } }) { glyph() }
                    } else {
                        glyph()
                    }
                },
                label = { Text(tab.label, fontWeight = if (selected) FontWeight.Bold else null) },
                colors = NavigationBarItemDefaults.colors(
                    indicatorColor = indicators[i],
                    selectedIconColor = glyphs[i],
                    selectedTextColor = MaterialTheme.colorScheme.onSurface,
                ),
            )
        }
    }
}
