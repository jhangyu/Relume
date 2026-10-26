package io.github.jhangyu.relume.ui

import androidx.annotation.DrawableRes
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.material3.Surface
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
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
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
                    RunSteps()
                }
                SectionHeader("OTA push")
                InsetGroup {
                    if (model.otaSchedules.isEmpty()) ListRow("No OTA schedules", supporting = "Push new builds to ADB phones on a timer")
                    model.otaSchedules.forEachIndexed { i, o ->
                        if (i > 0) Hairline(Modifier.padding(start = 16.dp))
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

/** Run pipeline (mockup .steps): 40 dp category-gradient tiles joined by a 2 dp connector, on the grey surface. */
@Composable
private fun RunSteps() {
    val r = MaterialTheme.relume
    // Mockup data-cat per step: productivity, personalization, social, music, maps (tokens.md section 3).
    val pastels = listOf(r.blue to r.teal, r.lavender to r.pink, r.pink to r.lavender, r.amber to r.coral, r.green to r.teal)
    val connector = MaterialTheme.colorScheme.outlineVariant
    Column {
        RUN_STEPS.forEachIndexed { i, st ->
            val (c1, c2) = pastels[i]
            val last = i == RUN_STEPS.lastIndex
            Row(
                Modifier.fillMaxWidth().heightIn(min = 72.dp).drawBehind {
                    // ponytail: the line runs 4 dp below this tile to 4 dp above the next one, assuming the next row is 72 dp too.
                    if (!last) {
                        val x = STEP_TILE.toPx() / 2 - CONNECTOR_WIDTH.toPx() / 2
                        val top = size.height / 2 + STEP_TILE.toPx() / 2 + CONNECTOR_GAP.toPx()
                        val bottom = size.height + (72.dp - STEP_TILE).toPx() / 2 - CONNECTOR_GAP.toPx()
                        drawRect(connector, Offset(x, top), Size(CONNECTOR_WIDTH.toPx(), bottom - top))
                    }
                }.padding(vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    Modifier.size(STEP_TILE).clip(RoundedCornerShape(12.dp)).background(Brush.linearGradient(listOf(c1.container, c2.container))),
                    contentAlignment = Alignment.Center,
                ) {
                    Glyph(st.icon, c1.onContainer, 20.dp)
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Txt(st.title, maxLines = 1)
                    Txt(st.detail, style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 2)
                }
            }
        }
    }
}

private val STEP_TILE = 40.dp
/** Mockup interval tag: 32 dp, taller than a plain tag. */
private val INTERVAL_TAG_HEIGHT = 32.dp
private val CONNECTOR_WIDTH = 2.dp
private val CONNECTOR_GAP = 4.dp

/** Mockup .gcard: white, 28 dp corner, 16 dp padding, 8 dp gaps, start-aligned; chips wrap inside the card. */
@Composable
private fun IntervalCard(schedule: Schedule, model: AppModel, onCustom: () -> Unit) {
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.surfaceContainerLowest) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Txt("Keep apps updated", style = MaterialTheme.typography.titleMedium)
            Pill(describeSpec(schedule.spec), MaterialTheme.relume.blue.container, MaterialTheme.relume.blue.onContainer, R.drawable.ms_schedule, INTERVAL_TAG_HEIGHT)
            Txt(
                if (schedule.enabled) "Next run ${formatTime(schedule.nextRunAt)}" else "Off: runs only with Run now (turn on in the web Schedules page)",
                style = MaterialTheme.typography.bodySmall, color = muted(),
            )
            val labels = INTERVAL_PRESETS.map { "$it h" } + "Custom"
            // The wrapping ChipRow brings its own 16 dp side insets; full-bleed puts the chips on the card's content edge.
            Box(Modifier.fullBleed()) {
                ChipRow(labels, presetIndex(schedule.spec) ?: INTERVAL_PRESETS.size, wrap = true) { i ->
                    if (i == INTERVAL_PRESETS.size) onCustom()
                    else model.putSchedule(buildJsonObject { put("spec", buildJsonObject { put("every", EVERY_HOURS); put("hours", INTERVAL_PRESETS[i]) }) })
                }
            }
            ActionButton("Run now", model::runSchedule, icon = R.drawable.ms_play_arrow)
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
            Pill(describeSpec(o.spec), tagColors.container, tagColors.onContainer, height = ListPillHeight)
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
private fun SpecForm(
    every: String, hours: String, at: String, days: Set<Int>,
    onEvery: (String) -> Unit, onHours: (String) -> Unit, onAt: (String) -> Unit, onDays: (Set<Int>) -> Unit,
    side: (@Composable RowScope.() -> Unit)? = null,
) {
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
    // Mockup sheet: At and Retries side by side, 16 dp apart.
    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        if (every == EVERY_HOURS) {
            Field("Every (hours, 1–$MAX_HOURS)", hours, onHours, Modifier.weight(1f), mono = true, keyboard = KeyboardOptions(keyboardType = KeyboardType.Number))
        } else {
            Field("At (HH:MM)", at, onAt, Modifier.weight(1f), mono = true, error = if (at.isNotEmpty() && !AT.matches(at)) "Expected 24-hour time like 03:00" else null)
        }
        side?.invoke(this)
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
            Modifier.verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Txt(title, style = MaterialTheme.typography.headlineSmall)
            content()
        }
    }
}

private val RETRIES_WIDTH = 112.dp

/** Sheet actions (mockup .sheet .acts): text Cancel and a compact filled button, right-aligned, 8 dp apart. */
@Composable
private fun SheetButtons(content: @Composable RowScope.() -> Unit) {
    Row(Modifier.fillMaxWidth().padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End), content = content)
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
        SheetButtons {
            ActionButton("Cancel", onDismiss, kind = ButtonKind.Text)
            ActionButton("Save", { spec?.let(onSave) }, enabled = spec != null)
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
        SpecForm(every, hours, at, days, { every = it }, { hours = it }, { at = it }, { days = it }) {
            Field("Retries", retries, { retries = it }, Modifier.width(RETRIES_WIDTH), mono = true, keyboard = KeyboardOptions(keyboardType = KeyboardType.Number),
                error = if (retries.isNotEmpty() && retryCount == null) "0 to $MAX_RETRIES" else null, hint = "0 to $MAX_RETRIES")
        }
        SectionHeader("Phones")
        if (adb.isEmpty()) Txt("No ADB phones. Pair one on the web Devices page.", color = muted())
        adb.forEach { d -> CheckRow(d.name, d.id in phones) { on -> phones = if (on) phones + d.id else phones - d.id } }
        SheetButtons {
            ActionButton("Cancel", onClose, kind = ButtonKind.Text)
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
            }, enabled = ok)
        }
    }
}
