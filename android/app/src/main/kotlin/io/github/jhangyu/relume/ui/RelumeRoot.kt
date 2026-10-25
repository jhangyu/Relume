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
import androidx.compose.runtime.Composable
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

private val TABS = listOf(
    NavItem("Apps", Icons.Apps) to Screen.Apps,
    NavItem("Builds", Icons.Builds) to Screen.Builds,
    NavItem("Settings", Icons.Settings) to Screen.Settings,
)

/** Spec 10 navigation: a saved back stack of [Screen]s and a back handler. Tabs are stack roots, Login replaces everything. */
@Composable
fun RelumeRoot() {
    FluentTheme {
        val c = FluentTheme.colors
        Column(Modifier.fillMaxSize().background(c.bg).windowInsetsPadding(WindowInsets.systemBars)) {
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
    Column(Modifier.weight(1f)) {
        when (current) {
            Screen.Apps -> AppsScreen(
                model,
                onOpenDetail = { stack = stack + Screen.Detail(it) },
                onOpenLog = { job, item -> stack = stack + Screen.Log(job, item) },
                onInstall = model::beginInstall,
            )
            Screen.Builds -> model.api?.let { api ->
                BuildsScreen(api, labelOf = { id -> id?.let(model::item)?.label }, onOpen = { stack = stack + Screen.Log(it, null) }, reloadKey = model.buildsVersion)
            }
            Screen.Settings -> SettingsScreen(model, notificationsOn, askNotifications)
            is Screen.Detail -> DetailScreen(
                model, current.itemId,
                onBack = { stack = stack.dropLast(1) },
                onOpenLog = { job, item -> stack = stack + Screen.Log(job, item) },
                onInstall = model::beginInstall,
            )
            is Screen.Log -> model.api?.let { api ->
                val item = current.itemId?.let(model::item)
                LogScreen(api, current.jobId, item?.activeJob?.takeIf { it.jobId == current.jobId }, item?.label, { stack = stack.dropLast(1) }, model::buildEnded)
            }
            Screen.Install -> InstallScreen(run, model) { stack = stack.dropLast(1) }
        }
    }
    if (current.isRoot) {
        BottomNav(TABS.map { it.first }, TABS.indexOfFirst { it.second == current }) { stack = listOf(TABS[it].second) }
    }
    InstallPrompts(model)
}
