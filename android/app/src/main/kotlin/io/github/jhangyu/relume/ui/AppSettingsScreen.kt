package io.github.jhangyu.relume.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.api.AppSettings
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private val VERSION_MODES = listOf("auto", "pinned", "latest")

internal fun versionBody(mode: String, pin: String?, allowNearest: Boolean): JsonObject = buildJsonObject {
    put("version", buildJsonObject {
        put("mode", mode)
        pin?.let { put("pin", it) }
        put("allow_nearest", allowNearest)
    })
}

/** App settings (thin first version): enable switch (off disables and leaves) and the version mode. */
@Composable
fun AppSettingsScreen(model: AppModel, appId: Int, onBack: () -> Unit, onOpenLog: (Int, Int?) -> Unit) {
    val app = model.app(appId)
    var settings by remember(appId) { mutableStateOf<AppSettings?>(null) }
    LaunchedEffect(appId) { settings = model.call { it.app(appId) }?.settings }

    Column(Modifier.fillMaxSize()) {
        AppBar(app?.name ?: "App", app?.packageName, onBack = onBack)
        val loaded = settings
        if (app == null || loaded == null) {
            Txt("Loading…", Modifier.padding(ScreenPadding), color = muted())
            return@Column
        }
        var mode by remember(loaded) { mutableStateOf(loaded.version.mode) }
        var pin by remember(loaded) { mutableStateOf(loaded.version.pin.orEmpty()) }
        var nearest by remember(loaded) { mutableStateOf(loaded.version.allowNearest) }
        val save = { rebuild: Boolean ->
            model.saveApp(appId, versionBody(mode, pin.trim().takeIf { mode == "pinned" && it.isNotEmpty() }, nearest), rebuild) { job ->
                if (job != null) onOpenLog(job, appId) else onBack()
            }
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            InsetGroup {
                SwitchRow("Enabled", "Off removes the app from this list. Its settings stay.", checked = true) { model.disable(appId, onBack) }
            }
            SectionHeader("App version")
            ChipRow(VERSION_MODES.map { it.replaceFirstChar(Char::titlecase) }, VERSION_MODES.indexOf(mode)) { mode = VERSION_MODES[it] }
            if (mode == "pinned") Field("Pinned version", pin, { pin = it }, mono = true)
            InsetGroup { SwitchRow("Allow nearest version", "Use the closest supported version when the pin is unavailable.", nearest) { nearest = it } }
        }
        Column(Modifier.fillMaxWidth().padding(ScreenPadding), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ActionButton("Save and rebuild", { save(true) }, Modifier.fillMaxWidth())
            ActionButton("Save", { save(false) }, Modifier.fillMaxWidth(), kind = ButtonKind.Outlined)
        }
    }
}
