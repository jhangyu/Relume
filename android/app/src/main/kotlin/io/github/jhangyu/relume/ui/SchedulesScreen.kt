package io.github.jhangyu.relume.ui

import androidx.annotation.DrawableRes
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.DeviceKind
import io.github.jhangyu.relume.api.OtaSchedule
import io.github.jhangyu.relume.api.Schedule
import io.github.jhangyu.relume.api.ScheduleSpec
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

private val WEEKDAYS = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

/** Interval chips of the update schedule, in hours (spec 7.5). */
internal val INTERVAL_PRESETS = listOf(1, 3, 6, 12, 24)
private val AT = Regex("^([01]\\d|2[0-3]):[0-5]\\d$")
private const val MAX_HOURS = 168
private const val MAX_RETRIES = 5
private const val EVERY_HOURS = "hours"
private const val EVERY_DAY = "day"
private const val EVERY_WEEK = "week"

internal fun describeSpec(spec: ScheduleSpec): String = when (spec.every) {
    EVERY_HOURS -> "Every ${spec.hours} hours"
    EVERY_DAY -> "Every day at ${spec.at}"
    EVERY_WEEK -> "Every ${spec.days.orEmpty().joinToString(", ") { WEEKDAYS[it] }} at ${spec.at}"
    else -> throw IllegalArgumentException("schedule spec.every: expected hours, day or week, got '${spec.every}'")
}

/** Index of the preset chip that equals [spec], or null (shown as Custom). */
internal fun presetIndex(spec: ScheduleSpec): Int? =
    if (spec.every == EVERY_HOURS) INTERVAL_PRESETS.indexOf(spec.hours).takeIf { it >= 0 } else null

/** The `spec` the server accepts (scheduler `_check_spec`), or null while the form is incomplete or out of range. */
internal fun specJson(every: String, hours: String, at: String, days: Set<Int>): JsonObject? = when (every) {
    EVERY_HOURS -> hours.toIntOrNull()?.takeIf { it in 1..MAX_HOURS }?.let { h -> buildJsonObject { put("every", EVERY_HOURS); put("hours", h) } }
    EVERY_DAY -> at.takeIf(AT::matches)?.let { buildJsonObject { put("every", EVERY_DAY); put("at", it) } }
    EVERY_WEEK -> at.takeIf { AT.matches(it) && days.isNotEmpty() }?.let {
        buildJsonObject {
            put("every", EVERY_WEEK)
            put("days", buildJsonArray { days.sorted().forEach { d -> add(JsonPrimitive(d)) } })
            put("at", it)
        }
    }
    else -> throw IllegalArgumentException("schedule form: expected every hours, day or week, got '$every'")
}

/** Steps of every update run (apps part 5), shown under the last run time. */
private class RunStep(val title: String, val detail: String, @DrawableRes val icon: Int)

private val RUN_STEPS = listOf(
    RunStep("Refresh catalog", "Official catalog and custom sources", R.drawable.ms_storefront),
    RunStep("Check chosen bundles", "New patch releases of enabled apps", R.drawable.ms_inventory_2),
    RunStep("Check APKMirror", "New app versions; finds missing pages", R.drawable.ms_search),
    RunStep("Rebuild what changed", "Apps whose inputs changed", R.drawable.ms_build),
    RunStep("Notify", "When the run has news", R.drawable.ms_notifications),
)

private sealed interface Sheet {
    data object Custom : Sheet
    class Ota(val edit: OtaSchedule?) : Sheet
}

/** Schedules tab: the global update interval with Run now and the run pipeline, then the OTA push schedules. */
@Composable
fun SchedulesScreen(model: AppModel) {
    val schedule: Schedule? = model.schedule
    var sheet by remember { mutableStateOf<Sheet?>(null) }
    LaunchedEffect(Unit) { model.loadDevices() }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            AppBar("Schedules", actions = { Avatar() })
            model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(start = ScreenPadding, end = ScreenPadding, bottom = FAB_CLEARANCE),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                if (schedule == null) {
                    Txt("Loading…", color = muted())
                } else {
                    IntervalCard(schedule, model) { sheet = Sheet.Custom }
                    SectionHeader("Last run ${formatTime(schedule.lastRunAt)}")
                    InsetGroup {
                        val pastels = MaterialTheme.relume.let { listOf(it.blue, it.lavender, it.pink, it.amber, it.green) }
                        RUN_STEPS.forEachIndexed { i, st ->
                            if (i > 0) Hairline()
                            ListRow(st.title, supporting = st.detail, leading = {
                                Box(Modifier.size(40.dp).clip(CircleShape).background(pastels[i].container), contentAlignment = Alignment.Center) {
                                    Glyph(st.icon, pastels[i].onContainer)
                                }
                            })
                        }
                    }
                }
                SectionHeader("OTA push")
                InsetGroup {
                    if (model.otaSchedules.isEmpty()) ListRow("No OTA schedules", supporting = "Push new builds to ADB phones on a timer")
                    model.otaSchedules.forEachIndexed { i, o ->
                        if (i > 0) Hairline()
                        OtaRow(o, model) { sheet = Sheet.Ota(o) }
                    }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = { sheet = Sheet.Ota(null) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(ScreenPadding),
            containerColor = MaterialTheme.colorScheme.tertiaryContainer,
            contentColor = MaterialTheme.colorScheme.onTertiaryContainer,
        ) {
            Glyph(R.drawable.ms_add, MaterialTheme.colorScheme.onTertiaryContainer)
            Text("OTA schedule", Modifier.padding(start = 12.dp))
        }
    }
    when (val sh = sheet) {
        null -> Unit
        Sheet.Custom -> schedule?.let { s -> CustomSheet(s.spec, onDismiss = { sheet = null }) { spec -> model.putSchedule(buildJsonObject { put("spec", spec) }); sheet = null } }
        is Sheet.Ota -> OtaSheet(sh.edit, model) { sheet = null }
    }
}

private val FAB_CLEARANCE = 88.dp

@Composable
private fun IntervalCard(schedule: Schedule, model: AppModel, onCustom: () -> Unit) {
    InsetGroup {
        Column(Modifier.padding(vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Txt("Keep apps updated", style = MaterialTheme.typography.titleMedium)
                Pill(describeSpec(schedule.spec), MaterialTheme.relume.blue.container, MaterialTheme.relume.blue.onContainer, R.drawable.ms_schedule)
                Txt(
                    if (schedule.enabled) "Next run ${formatTime(schedule.nextRunAt)}" else "Off: runs only with Run now (turn on in the web Schedules page)",
                    style = MaterialTheme.typography.bodySmall, color = muted(),
                )
            }
            val labels = INTERVAL_PRESETS.map { "$it h" } + "Custom"
            ChipRow(labels, presetIndex(schedule.spec) ?: INTERVAL_PRESETS.size) { i ->
                if (i == INTERVAL_PRESETS.size) onCustom()
                else model.putSchedule(buildJsonObject { put("spec", buildJsonObject { put("every", EVERY_HOURS); put("hours", INTERVAL_PRESETS[i]) }) })
            }
            ActionButton("Run now", model::runSchedule, Modifier.padding(horizontal = 16.dp).fillMaxWidth(), icon = R.drawable.ms_play_arrow)
        }
    }
}

@Composable
private fun OtaRow(o: OtaSchedule, model: AppModel, onEdit: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    val ids = o.target["device_ids"]?.jsonArray?.map { it.jsonPrimitive.content.toInt() }
    val phones = if (ids == null) "All ADB phones" else ids.joinToString { id -> model.devices.firstOrNull { it.id == id }?.name ?: "phone $id" }
    val tagColors = MaterialTheme.relume.let { if (o.spec.every == EVERY_WEEK) it.lavender else it.amber }
    ListRow(o.name, supporting = phones, trailing = {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill(describeSpec(o.spec), tagColors.container, tagColors.onContainer)
            Switch(checked = o.enabled, onCheckedChange = { model.setOtaEnabled(o.id, it) }, Modifier.padding(horizontal = 8.dp))
            Box {
                IconAction(R.drawable.ms_more_vert, "More for ${o.name}", { menu = true })
                DropdownMenu(menu, { menu = false }) {
                    DropdownMenuItem(text = { Text("Edit") }, onClick = { menu = false; onEdit() })
                    DropdownMenuItem(text = { Text("Delete") }, onClick = { menu = false; model.deleteOta(o.id) })
                }
            }
        }
    })
}

/** Repeat form shared by both sheets: Hourly (every n hours), Daily (at), Weekly (days + at). */
@Composable
private fun SpecForm(every: String, hours: String, at: String, days: Set<Int>, onEvery: (String) -> Unit, onHours: (String) -> Unit, onAt: (String) -> Unit, onDays: (Set<Int>) -> Unit) {
    val kinds = listOf(EVERY_HOURS to "Hourly", EVERY_DAY to "Daily", EVERY_WEEK to "Weekly")
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        kinds.forEachIndexed { i, (k, label) ->
            SegmentedButton(
                selected = every == k,
                onClick = { onEvery(k) },
                shape = SegmentedButtonDefaults.itemShape(i, kinds.size),
                colors = SegmentedButtonDefaults.colors(
                    activeContainerColor = MaterialTheme.colorScheme.secondaryContainer,
                    activeContentColor = MaterialTheme.colorScheme.onSecondaryContainer,
                ),
                label = { Text(label) },
            )
        }
    }
    if (every == EVERY_HOURS) {
        Field("Every (hours, 1–$MAX_HOURS)", hours, onHours, mono = true, keyboard = KeyboardOptions(keyboardType = KeyboardType.Number))
    } else {
        Field("At (HH:MM)", at, onAt, mono = true, error = if (at.isNotEmpty() && !AT.matches(at)) "Expected 24-hour time like 03:00" else null)
    }
    if (every == EVERY_WEEK) {
        val s = MaterialTheme.colorScheme
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            WEEKDAYS.forEachIndexed { i, d ->
                FilterChip(
                    selected = i in days,
                    onClick = { onDays(if (i in days) days - i else days + i) },
                    label = { Text(d) },
                    colors = FilterChipDefaults.filterChipColors(selectedContainerColor = s.secondaryContainer, selectedLabelColor = s.onSecondaryContainer),
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SheetFrame(title: String, onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
    ) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(start = 24.dp, end = 24.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Txt(title, style = MaterialTheme.typography.headlineSmall)
            content()
        }
    }
}

/** Custom interval of the update schedule (day / week / hours; no cron, the scheduler has none). */
@Composable
private fun CustomSheet(current: ScheduleSpec, onDismiss: () -> Unit, onSave: (JsonObject) -> Unit) {
    var every by remember { mutableStateOf(current.every) }
    var hours by remember { mutableStateOf(current.hours?.toString().orEmpty()) }
    var at by remember { mutableStateOf(current.at ?: "09:00") }
    var days by remember { mutableStateOf(current.days.orEmpty().toSet()) }
    val spec = specJson(every, hours, at, days)
    SheetFrame("Custom interval", onDismiss) {
        SpecForm(every, hours, at, days, { every = it }, { hours = it }, { at = it }, { days = it })
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Save", { spec?.let(onSave) }, Modifier.weight(1f), enabled = spec != null)
        }
    }
}

/** New or edited OTA schedule. Phones are ADB devices: the server refuses phones that install through the app. */
@Composable
private fun OtaSheet(edit: OtaSchedule?, model: AppModel, onClose: () -> Unit) {
    var name by remember { mutableStateOf(edit?.name.orEmpty()) }
    var every by remember { mutableStateOf(edit?.spec?.every ?: EVERY_DAY) }
    var hours by remember { mutableStateOf(edit?.spec?.hours?.toString() ?: "24") }
    var at by remember { mutableStateOf(edit?.spec?.at ?: "03:00") }
    var days by remember { mutableStateOf(edit?.spec?.days.orEmpty().toSet()) }
    var retries by remember { mutableStateOf((edit?.options?.retries ?: 0).toString()) }
    val adb = model.devices.filter { it.kind == DeviceKind.ADB }
    var phones by remember {
        mutableStateOf(edit?.target?.get("device_ids")?.jsonArray?.map { it.jsonPrimitive.content.toInt() }?.toSet() ?: adb.map { it.id }.toSet())
    }
    val spec = specJson(every, hours, at, days)
    val retryCount = retries.toIntOrNull()?.takeIf { it in 0..MAX_RETRIES }
    val ok = name.isNotBlank() && spec != null && retryCount != null && phones.isNotEmpty()
    SheetFrame(if (edit == null) "New OTA schedule" else "Edit ${edit.name}", onClose) {
        Field("Name", name, { name = it })
        SectionHeader("Repeat")
        SpecForm(every, hours, at, days, { every = it }, { hours = it }, { at = it }, { days = it })
        Field("Retries (0–$MAX_RETRIES)", retries, { retries = it }, mono = true, keyboard = KeyboardOptions(keyboardType = KeyboardType.Number),
            error = if (retries.isNotEmpty() && retryCount == null) "Expected 0 to $MAX_RETRIES" else null)
        SectionHeader("Phones")
        if (adb.isEmpty()) Txt("No ADB phones. Pair one on the web Devices page.", color = muted())
        adb.forEach { d -> CheckRow(d.name, d.id in phones) { on -> phones = if (on) phones + d.id else phones - d.id } }
        DialogButtons {
            ActionButton("Cancel", onClose, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton(if (edit == null) "Create" else "Save", {
                val body = buildJsonObject {
                    put("name", name.trim())
                    put("kind", "ota")
                    put("target", buildJsonObject { put("device_ids", buildJsonArray { phones.sorted().forEach { add(JsonPrimitive(it)) } }) })
                    put("spec", checkNotNull(spec) { "OTA sheet: Create is enabled only with a complete spec" })
                    put("options", buildJsonObject {
                        put("retries", checkNotNull(retryCount) { "OTA sheet: Create is enabled only with valid retries" })
                        edit?.options?.notify?.let { put("notify", it) }   // the sheet does not edit notify: keep it
                    })
                }
                model.saveOta(edit?.id, body, onClose)
            }, Modifier.weight(1f), enabled = ok)
        }
    }
}
