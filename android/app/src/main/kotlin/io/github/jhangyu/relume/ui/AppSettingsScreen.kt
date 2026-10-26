package io.github.jhangyu.relume.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.PatchOption
import io.github.jhangyu.relume.api.PatchView
import io.github.jhangyu.relume.api.PatchesView
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

private val VERSION_MODES = listOf("auto", "pinned", "latest")
private val ABIS = listOf("universal", "arm64-v8a", "armeabi-v7a", "x86", "x86_64")
private val MAX_SIGBYPASS = mapOf("lspatch" to 3, "npatch" to 4)
/** Lookup states the manual retry refuses (409): the URL is already known. */
private val LOOKUP_DONE = setOf("found", "manual")

internal fun isModule(engine: String) = engine in MAX_SIGBYPASS

/** `patch_overrides` entries from the edited patch list: only switches and option values that differ from the default (web `overridesFrom`). */
internal fun overridesFrom(patches: List<PatchView>): JsonObject = buildJsonObject {
    for (p in patches) {
        val o = buildJsonObject {
            if (p.enabled != p.defaultEnabled && p.enabled != null) put("enabled", p.enabled)
            val opts = p.options.filter { it.value != it.default }
            if (opts.isNotEmpty()) put("options", buildJsonObject { opts.forEach { put(it.key, it.value ?: JsonNull) } })
        }
        if (o.isNotEmpty()) put(p.name, o)
    }
}

/** Variants for the chosen ABIs: kept ones unchanged, new ones copied from the first (web item.js rule). */
internal fun variantsFor(current: List<JsonObject>, abis: Set<String>, engine: String): List<JsonObject> {
    val kept = current.filter { it["abi"]?.jsonPrimitive?.content in abis }
    val have = kept.map { it["abi"]?.jsonPrimitive?.content }.toSet()
    val template = current.first()
    return kept + abis.filter { it !in have }.sortedBy(ABIS::indexOf).map { abi ->
        JsonObject(template + mapOf(
            "name" to JsonPrimitive(abi),
            "abi" to JsonPrimitive(abi),
            "strip_libs" to if (abi == "universal" || engine != "morphe") JsonNull else JsonPrimitive(abi),
        ))
    }
}

/** App settings: the same fields as the web Apps drawer (spec 7.4), Save and Save & rebuild. */
@Composable
fun AppSettingsScreen(model: AppModel, appId: Int, onBack: () -> Unit, onOpenLog: (Int, Int?) -> Unit) {
    val app = model.app(appId)
    var data by remember(appId) { mutableStateOf<AppModel.SettingsData?>(null) }
    LaunchedEffect(appId) { data = model.loadSettings(appId) }

    Column(Modifier.fillMaxSize()) {
        AppBar(app?.name ?: "App", app?.packageName, onBack = onBack, actions = {
            if (app != null) Switch(checked = true, onCheckedChange = { model.disable(appId, onBack) }, Modifier.padding(end = 12.dp))
        })
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        val d = data
        if (app == null || d == null) {
            Txt(if (app == null) "This app is not enabled." else "Loading…", Modifier.padding(ScreenPadding), color = muted())
            return@Column
        }
        val s = d.detail.settings
        val engine = app.bundle.engine
        var bundleId by remember(d) { mutableStateOf(app.bundle.bundleId) }
        var patches by remember(d) { mutableStateOf(d.patches?.patches.orEmpty()) }
        var mode by remember(d) { mutableStateOf(s.version.mode) }
        var pin by remember(d) { mutableStateOf(s.version.pin.orEmpty()) }
        var nearest by remember(d) { mutableStateOf(s.version.allowNearest) }
        val abisNow = s.variants.mapNotNull { it["abi"]?.jsonPrimitive?.content }.toSet()
        var abis by remember(d) { mutableStateOf(abisNow) }
        val sigNow = s.engineOptions?.get("sigbypass")?.jsonPrimitive?.content?.toInt()
        val dexNow = s.engineOptions?.get("injectdex")?.jsonPrimitive?.booleanOrNull
        var sigbypass by remember(d) { mutableStateOf(sigNow) }
        var injectdex by remember(d) { mutableStateOf(dexNow) }
        var deps by remember(d) { mutableStateOf(s.projectIds.toSet()) }
        var name by remember(d) { mutableStateOf(s.displayName.orEmpty()) }
        val dl = d.detail.app.download
        var listUrl by remember(d) { mutableStateOf(dl.apkmirrorListUrl.orEmpty()) }
        var pureUrl by remember(d) { mutableStateOf(dl.apkpureUrl.orEmpty()) }
        var badOptions by remember(d) { mutableStateOf(emptySet<String>()) }

        val bundleChanged = bundleId != app.bundle.bundleId
        val pinMissing = mode == "pinned" && pin.isBlank()
        val save = { rebuild: Boolean ->
            val body = buildJsonObject {
                if (bundleChanged) put("bundle_id", bundleId)
                if (mode != s.version.mode || pin != s.version.pin.orEmpty() || nearest != s.version.allowNearest) {
                    put("version", buildJsonObject {
                        put("mode", mode)
                        if (mode == "pinned") put("pin", pin.trim())
                        put("allow_nearest", nearest)
                    })
                }
                if (abis != abisNow && !bundleChanged) put("variants", JsonArray(variantsFor(s.variants, abis, engine)))
                if ((sigbypass != sigNow || injectdex != dexNow) && !bundleChanged) {
                    put("engine_options", buildJsonObject { put("sigbypass", sigbypass); put("injectdex", injectdex) })
                }
                if (deps != s.projectIds.toSet()) put("project_ids", buildJsonArray { deps.sorted().forEach { add(JsonPrimitive(it)) } })
                if (name.trim() != s.displayName.orEmpty()) put("display_name", name.trim().ifEmpty { null })
                if (listUrl.trim() != dl.apkmirrorListUrl.orEmpty() || pureUrl.trim() != dl.apkpureUrl.orEmpty()) {
                    put("download", buildJsonObject {
                        put("apkmirror_list_url", listUrl.trim().ifEmpty { null })
                        put("apkpure_url", pureUrl.trim().ifEmpty { null })
                    })
                }
            }
            val edited = overridesFrom(patches)
            val overrides = if (!bundleChanged && d.patches != null && edited != overridesFrom(d.patches.patches)) edited else null
            model.saveApp(appId, body.takeIf { it.isNotEmpty() }, overrides, rebuild) { job ->
                if (job != null) onOpenLog(job, appId) else onBack()
            }
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SectionHeader("Patch bundle")
            InsetGroup {
                val bundles = d.bundles
                if (bundles == null) {
                    ListRow(app.bundle.name, supporting = "Other bundles unavailable: the catalog has no entry for this package")
                } else {
                    bundles.forEachIndexed { i, b ->
                        if (i > 0) Hairline()
                        RadioRow(bundleId == b.bundleId, enabled = b.selectable, onSelect = { bundleId = b.bundleId }) {
                            Column(Modifier.weight(1f)) {
                                Txt(b.name, maxLines = 1)
                                Txt(
                                    b.notSelectableReason ?: listOf(b.engine, "${b.patches.size} patches").joinToString(" · "),
                                    style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 1,
                                )
                            }
                            OriginLabel(b.origin)
                        }
                    }
                }
            }
            if (bundleChanged) Banner("Bundle changes on save", Tone.Info, detail = "Patches, variants and module options of the new bundle can be edited after saving. Same-name patch choices are kept.")

            if (!bundleChanged && isModule(engine)) {
                SectionHeader("Xposed module")
                InsetGroup {
                    val max = MAX_SIGBYPASS.getValue(engine)
                    Txt("Signature bypass level", Modifier.padding(start = 16.dp, top = 12.dp))
                    ChipRow((0..max).map(Int::toString), sigbypass ?: 0) { sigbypass = it }
                    Hairline()
                    SwitchRow("Inject dex", null, injectdex ?: false) { injectdex = it }
                }
            }
            if (!bundleChanged && !isModule(engine)) {
                PatchSection(d.patches, patches, s.staleOverrides, badOptions, onChange = { patches = it }, onBad = { badOptions = it })
            }

            SectionHeader("Version")
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                VERSION_MODES.forEachIndexed { i, m ->
                    SegmentedButton(
                        selected = mode == m,
                        onClick = { mode = m },
                        shape = SegmentedButtonDefaults.itemShape(i, VERSION_MODES.size),
                        colors = SegmentedButtonDefaults.colors(
                            activeContainerColor = MaterialTheme.colorScheme.secondaryContainer,
                            activeContentColor = MaterialTheme.colorScheme.onSecondaryContainer,
                        ),
                        label = { Text(m.replaceFirstChar(Char::titlecase)) },
                    )
                }
            }
            val supported = s.version.supported
            Txt(
                when (mode) {
                    "auto" -> "Auto: newest version the bundle supports" + (supported.firstOrNull()?.let { " · $it" } ?: " · any")
                    "latest" -> "Latest: newest version on APKMirror" + (s.version.apkmirror.firstOrNull()?.let { " · $it" }.orEmpty())
                    else -> "Pinned: always this version" + if (supported.isEmpty()) "" else " · supported: ${supported.take(SUPPORTED_SHOWN).joinToString()}"
                },
                style = MaterialTheme.typography.bodySmall, color = muted(),
            )
            if (mode == "pinned") Field("Pinned version", pin, { pin = it }, mono = true, error = if (pinMissing) "A pinned version is required" else null)
            InsetGroup { SwitchRow("Allow nearest version", "Build the closest one if the exact version is missing", nearest) { nearest = it } }

            if (!bundleChanged) {
                SectionHeader("ABI variants")
                FlowChips(ABIS, abis) { abi ->
                    abis = if (abi in abis) (abis - abi).ifEmpty { abis } else abis + abi
                }
            }

            SectionHeader("Depends on Followed projects")
            InsetGroup {
                if (d.projects.isEmpty()) ListRow("No followed projects")
                d.projects.forEachIndexed { i, p ->
                    if (i > 0) Hairline()
                    val on = p.id in deps
                    ListRow(
                        p.label,
                        Modifier.toggleable(value = on, role = Role.Checkbox) { deps = if (it) deps + p.id else deps - p.id },
                        supporting = p.downloadedTag ?: p.repo,
                        leading = { AppTile(p.label, null, model.api) },
                        trailing = { Checkbox(checked = on, onCheckedChange = null, colors = CheckboxDefaults.colors(checkedColor = MaterialTheme.colorScheme.secondary)) },
                    )
                }
            }
            Txt("Install and OTA push bring these along.", style = MaterialTheme.typography.bodySmall, color = muted())

            SectionHeader("Download URLs")
            Row(verticalAlignment = Alignment.CenterVertically) {
                Field("APKMirror listing", listUrl, { listUrl = it }, Modifier.weight(1f), leadingIcon = R.drawable.ms_link, mono = true)
                IconAction(R.drawable.ms_search, "Locate APKMirror page again", { model.locate(appId) }, IconTone.Sync, enabled = dl.state !in LOOKUP_DONE)
            }
            LookupPill(dl.state)
            if (dl.state == LOOKUP_AMBIGUOUS) {
                InsetGroup {
                    dl.candidates.forEach { c ->
                        RadioRow(listUrl == c.listUrl, onSelect = { listUrl = c.listUrl }) {
                            Column(Modifier.weight(1f)) {
                                Txt(c.title, maxLines = 1)
                                Txt(c.url, style = Mono, color = muted(), maxLines = 1)
                            }
                        }
                    }
                }
            }
            dl.error?.let { Txt(it, style = MaterialTheme.typography.bodySmall, color = muted()) }
            Field("APKPure (optional)", pureUrl, { pureUrl = it }, leadingIcon = R.drawable.ms_link, mono = true)

            SectionHeader("Display name")
            Field("Name in lists", name, { name = it }, hint = "Empty = the catalog name")
        }
        Row(
            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer).padding(horizontal = ScreenPadding, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            val ok = !pinMissing && badOptions.isEmpty()
            ActionButton("Save", { save(false) }, Modifier.weight(1f), kind = ButtonKind.Outlined, enabled = ok)
            ActionButton("Save & rebuild", { save(true) }, Modifier.weight(1f), enabled = ok, icon = R.drawable.ms_build)
        }
    }
}

private const val SUPPORTED_SHOWN = 3

@Composable
private fun LookupPill(state: String) {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    when (state) {
        "found" -> Pill("Found automatically", r.green.container, r.green.onContainer, R.drawable.ms_check)
        "manual" -> Pill("Set by you", r.green.container, r.green.onContainer, R.drawable.ms_check)
        "pending" -> Pill("Lookup on the next update run", s.surfaceContainerHigh, s.onSurfaceVariant)
        LOOKUP_AMBIGUOUS -> Pill("Pick one", r.amber.container, r.amber.onContainer, R.drawable.ms_link)
        "blocked" -> Pill("APKMirror blocked the lookup", r.amber.container, r.amber.onContainer, R.drawable.ms_warning)
        "not_found", "failed" -> Pill("Needs URL", r.amber.container, r.amber.onContainer, R.drawable.ms_warning)
        else -> throw IllegalArgumentException("download.state: expected pending, found, manual, not_found, ambiguous, blocked or failed, got '$state'")
    }
}

/** ABI chips: the selected ones are on; the last one cannot be turned off (one variant minimum). */
@Composable
private fun FlowChips(all: List<String>, on: Set<String>, onToggle: (String) -> Unit) {
    val s = MaterialTheme.colorScheme
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        all.forEach { abi ->
            FilterChip(
                selected = abi in on,
                onClick = { onToggle(abi) },
                label = { Text(abi) },
                leadingIcon = if (abi in on) ({ Glyph(R.drawable.ms_check, s.onSecondaryContainer, 18.dp) }) else null,
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = s.secondaryContainer,
                    selectedLabelColor = s.onSecondaryContainer,
                ),
            )
        }
    }
}

/** Patch switches with inline option editors, "Changed by you" against the bundle default, Reset to defaults. */
@Composable
private fun PatchSection(
    view: PatchesView?,
    patches: List<PatchView>,
    stale: List<String>,
    bad: Set<String>,
    onChange: (List<PatchView>) -> Unit,
    onBad: (Set<String>) -> Unit,
) {
    if (view == null) return
    val on = patches.count { it.enabled == true }
    // Reset re-creates the editors, so their typed text follows the restored defaults.
    var resets by remember { mutableIntStateOf(0) }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f)) { SectionHeader("Patches · $on of ${patches.size} on") }
        ActionButton("Reset", {
            onChange(patches.map { p -> p.copy(enabled = p.defaultEnabled, options = p.options.map { it.copy(value = it.default) }) })
            onBad(emptySet())
            resets++
        }, kind = ButtonKind.Text)
    }
    if (view.unavailable) {
        Txt("The patch list of this bundle is not downloaded yet. It appears after the first update run.", color = muted())
        return
    }
    if (stale.isNotEmpty()) Banner("Kept from an earlier bundle", Tone.Info, detail = stale.joinToString())
    key(resets) { InsetGroup {
        patches.forEachIndexed { i, p ->
            if (i > 0) Hairline()
            val update = { q: PatchView -> onChange(patches.toMutableList().also { it[i] = q }) }
            val changed = p.enabled != p.defaultEnabled || p.options.any { it.value != it.default }
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Txt(p.name, maxLines = 2)
                    val sub = listOfNotNull(
                        p.description?.takeIf { it.isNotBlank() },
                        "default unknown".takeIf { p.defaultEnabled == null },
                        "${p.options.size} options".takeIf { p.options.size > 1 },
                    )
                    if (sub.isNotEmpty()) Txt(sub.joinToString(" · "), style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 2)
                    if (changed) Pill("Changed by you", MaterialTheme.relume.pink.container, MaterialTheme.relume.pink.onContainer)
                }
                Switch(checked = p.enabled == true, onCheckedChange = { update(p.copy(enabled = it)) })
            }
            if (p.enabled == true) {
                p.options.forEachIndexed { j, op ->
                    val id = "${p.name}/${op.key}"
                    Box(Modifier.padding(start = 16.dp, end = 16.dp, bottom = 8.dp)) {
                        OptionField(op, onValue = { v -> update(p.copy(options = p.options.toMutableList().also { it[j] = op.copy(value = v) })) }) { invalid ->
                            onBad(if (invalid) bad + id else bad - id)
                        }
                    }
                }
            }
        }
    } }
}

/** Editor by option type (web `fieldFor`): choice list, switch, one-per-line list, number, text. */
@Composable
private fun OptionField(op: PatchOption, onValue: (JsonElement?) -> Unit, onInvalid: (Boolean) -> Unit) {
    val type = op.type.orEmpty()
    val label = op.title ?: op.key
    val v = op.value
    when {
        op.values.isNotEmpty() -> {
            var open by remember { mutableStateOf(false) }
            Box {
                ListRow(label, supporting = (v as? JsonPrimitive)?.contentOrNull ?: "–", trailing = {
                    IconAction(R.drawable.ms_expand_more, "Choose $label", { open = true })
                })
                DropdownMenu(open, { open = false }) {
                    op.values.forEach { choice ->
                        DropdownMenuItem(text = { Text(choice) }, onClick = { onValue(JsonPrimitive(choice)); open = false })
                    }
                }
            }
        }
        Regex("bool", RegexOption.IGNORE_CASE).containsMatchIn(type) ->
            SwitchRow(label, null, (v as? JsonPrimitive)?.booleanOrNull ?: false) { onValue(JsonPrimitive(it)) }
        Regex("list|\\[]", RegexOption.IGNORE_CASE).containsMatchIn(type) -> {
            var text by remember(op.key) { mutableStateOf((v as? JsonArray)?.joinToString("\n") { it.jsonPrimitive.content }.orEmpty()) }
            OutlinedTextField(
                text,
                { t ->
                    text = t
                    onValue(JsonArray(t.lines().map(String::trim).filter(String::isNotEmpty).map(::JsonPrimitive)))
                },
                Modifier.fillMaxWidth(),
                label = { Text("$label (one per line)") },
                textStyle = Mono,
            )
        }
        Regex("int|long|float|double", RegexOption.IGNORE_CASE).containsMatchIn(type) -> {
            val decimal = Regex("float|double", RegexOption.IGNORE_CASE).containsMatchIn(type)
            var text by remember(op.key) { mutableStateOf((v as? JsonPrimitive)?.contentOrNull.orEmpty()) }
            val parsed: JsonPrimitive? = if (decimal) text.toDoubleOrNull()?.let(::JsonPrimitive) else text.toLongOrNull()?.let(::JsonPrimitive)
            val invalid = text.isNotBlank() && parsed == null
            Field(label, text, { t ->
                text = t
                val p = if (decimal) t.toDoubleOrNull()?.let(::JsonPrimitive) else t.toLongOrNull()?.let(::JsonPrimitive)
                onInvalid(t.isNotBlank() && p == null)
                if (t.isBlank()) onValue(JsonNull) else if (p != null) onValue(p)
            }, mono = true, error = if (invalid) "Expected ${if (decimal) "a number" else "a whole number"}" else null,
                hint = "Default: ${op.default ?: "none"}", keyboard = KeyboardOptions(keyboardType = KeyboardType.Number))
        }
        else -> Field(label, (v as? JsonPrimitive)?.contentOrNull.orEmpty(), { onValue(JsonPrimitive(it)) }, mono = true, hint = "Default: ${op.default ?: "none"}")
    }
}
