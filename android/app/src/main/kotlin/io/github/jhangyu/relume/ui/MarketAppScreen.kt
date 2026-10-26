package io.github.jhangyu.relume.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboard
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.BundleOption
import io.github.jhangyu.relume.api.CatalogPatch
import io.github.jhangyu.relume.api.DownloadState
import io.github.jhangyu.relume.api.MarketDetail
import kotlinx.coroutines.launch

/** What each `download.state` means for the user (spec 5.4). */
private fun DownloadState?.message(): String = when (this?.state) {
    null -> "No APK source yet. It is looked up on APKMirror when the first build runs."
    "pending" -> "Waiting for the APKMirror lookup."
    "found" -> "APKMirror listing found."
    "manual" -> "Using the APKMirror listing you set."
    "not_found" -> "APKMirror has no listing. Paste a URL below or pick another source."
    "ambiguous" -> "Several APKMirror listings match. Pick one in the app settings."
    "blocked" -> "APKMirror is rate limiting this server" + (this.retryAt?.let { ", retry at $it." } ?: ".")
    "failed" -> "APKMirror lookup failed" + (this.error?.let { ": $it" } ?: ".")
    else -> throw IllegalArgumentException("download.state: expected pending, found, manual, not_found, ambiguous, blocked or failed, got '${this?.state}'")
}

private const val BUNDLES_SHOWN = 3

/** App detail from the Marketplace: bundle radio list, patch switches, then the download-source step with "Enable and build". */
@Composable
fun MarketAppScreen(model: MarketModel, pkg: String, onBack: () -> Unit, onEnabled: (Int) -> Unit) {
    LaunchedEffect(pkg) { model.open(pkg) }
    val detail = model.detail?.takeIf { it.packageName == pkg }
    var chosen by remember(pkg) { mutableStateOf<Int?>(null) }
    var downloadStep by remember(pkg) { mutableStateOf(false) }
    var allBundles by remember(pkg) { mutableStateOf(false) }
    var mirrorUrl by remember(pkg) { mutableStateOf("") }
    var pureUrl by remember(pkg) { mutableStateOf("") }
    val bundle = detail?.bundles?.firstOrNull { it.bundleId == chosen }
    // System back walks the steps like the app bar arrow does.
    BackHandler(enabled = downloadStep) { downloadStep = false }

    Column(Modifier.fillMaxSize()) {
        AppBar(if (downloadStep) "Download source" else "", onBack = if (downloadStep) ({ downloadStep = false }) else onBack)
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        if (detail == null) {
            Txt(
                if (model.detailMissing) "This app is in no catalog yet. Refresh the catalog in the Marketplace tab." else "Loading…",
                Modifier.padding(ScreenPadding), color = muted(),
            )
            return@Column
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
            if (downloadStep && bundle != null) {
                DownloadStep(model, detail, bundle, mirrorUrl, { mirrorUrl = it }, pureUrl, { pureUrl = it })
            } else {
                Row(
                    Modifier.padding(start = ScreenPadding, end = ScreenPadding, bottom = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically,
                ) {
                    AppTile(detail.name, detail.iconUrl, model.api, size = 64.dp, category = detail.category)
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Txt(detail.name, style = MaterialTheme.typography.headlineSmall)
                        Txt(detail.packageName, style = Mono, color = muted(), maxLines = 1)
                        CategoryTag(detail.category)
                    }
                }
                detail.description?.let { Txt(it, Modifier.padding(horizontal = ScreenPadding), style = MaterialTheme.typography.bodyMedium, color = muted()) }
                val hidden = detail.bundles.size - BUNDLES_SHOWN
                HeaderRow("Choose one bundle", if (!allBundles && hidden > 0) "$hidden more" else null) { allBundles = true }
                InsetGroup(Modifier.padding(horizontal = ScreenPadding)) {
                    val shown = if (allBundles) detail.bundles else detail.bundles.take(BUNDLES_SHOWN)
                    shown.forEachIndexed { i, b ->
                        // Inset-group dividers start at the text, after the 16 dp inset, the 24 dp radio and the 16 dp gap.
                        if (i > 0) Hairline(Modifier.padding(start = BUNDLE_DIVIDER_START))
                        RadioRow(selected = chosen == b.bundleId, enabled = b.selectable, onSelect = { chosen = b.bundleId; model.clearPatchChanges() }) {
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                Txt(b.name, maxLines = 1)
                                Txt(
                                    b.notSelectableReason ?: listOfNotNull(b.stars?.let { "$it ★" }, plural(b.patches.size, "patch", "patches")).joinToString(" · "),
                                    style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 2,
                                )
                            }
                            OriginLabel(b.origin)
                        }
                    }
                }
                if (bundle != null) PatchList(model, bundle)
            }
        }
        Column(
            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer).padding(ScreenPadding),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (detail.enabled) {
                Txt("Enabled", color = muted())
            } else if (!downloadStep) {
                ActionButton("Next: download source", { downloadStep = true }, Modifier.fillMaxWidth(), enabled = bundle != null, size = ButtonSize.Large)
            } else {
                // Enable depends only on the bundle choice: the URLs are optional (spec 12 item 7).
                ActionButton(
                    "Enable and build", { model.enable(pkg, checkNotNull(chosen), mirrorUrl, pureUrl, onEnabled) }, Modifier.fillMaxWidth(),
                    enabled = chosen != null, icon = R.drawable.ms_check, size = ButtonSize.Large,
                )
            }
        }
    }
}

private val BUNDLE_DIVIDER_START = 56.dp

/** Section header with an optional text button; the button is pulled out 12 dp so its label ends on the content edge. */
@Composable
private fun HeaderRow(title: String, action: String?, onAction: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = TouchTarget).padding(start = ScreenPadding, end = 4.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f)) { SectionHeader(title) }
        if (action != null) ActionButton(action, onAction, kind = ButtonKind.Text)
    }
}

/** Effective switch of [p]: the user's change, else the catalog default (unknown counts as off). */
private fun MarketModel.isOn(p: CatalogPatch) = patchChanges[p.name] ?: p.defaultEnabled ?: false

/** Patches of the chosen bundle as a plain list on the surface: "Patches · N on" with Reset, switch rows with dividers. */
@Composable
private fun PatchList(model: MarketModel, bundle: BundleOption) {
    HeaderRow("Patches · ${bundle.patches.count { model.isOn(it) }} on", "Reset".takeIf { model.patchChanges.isNotEmpty() }, model::clearPatchChanges)
    bundle.patches.forEachIndexed { i, p ->
        if (i > 0) Hairline(Modifier.padding(start = ScreenPadding))
        val on = model.isOn(p)
        val help = listOfNotNull(p.description?.takeIf { it.isNotBlank() }, "default unknown".takeIf { p.defaultEnabled == null }).joinToString(" · ").ifEmpty { null }
        Row(
            Modifier.fillMaxWidth().heightIn(min = if (help == null) 56.dp else 72.dp)
                .toggleable(value = on, role = Role.Switch) { model.setPatch(p.name, it, p.defaultEnabled) }
                .padding(start = ScreenPadding, end = LIST_TRAILING_INSET, top = 8.dp, bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Txt(p.name, maxLines = 2)
                if (help != null) Txt(help, style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 2)
                if (p.name in model.patchChanges) Pill("Changed by you", MaterialTheme.relume.pink.container, MaterialTheme.relume.pink.onContainer, height = ListPillHeight)
            }
            Switch(checked = on, onCheckedChange = null)
        }
    }
}

private val LIST_TRAILING_INSET = 24.dp

/** Step 2: the chosen app and bundle, the lookup state, the optional URLs and what happens after Enable. */
@Composable
private fun DownloadStep(
    model: MarketModel, detail: MarketDetail, bundle: BundleOption,
    mirror: String, onMirror: (String) -> Unit, pure: String, onPure: (String) -> Unit,
) {
    val clipboard = LocalClipboard.current
    val scope = rememberCoroutineScope()
    val on = bundle.patches.count { model.isOn(it) }
    ListRow(
        detail.name,
        // A new app row starts in Auto version mode (schema default); a re-enabled one keeps its own mode.
        supporting = listOfNotNull(bundle.name, plural(on, "patch", "patches"), "Auto version".takeIf { detail.appId == null }).joinToString(" · "),
        leading = { AppTile(detail.name, detail.iconUrl, model.api, category = detail.category) },
    )
    Column(Modifier.padding(horizontal = ScreenPadding), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        if (detail.download?.state == "not_found") {
            Banner("APKMirror page not found", Tone.Warn, detail = detail.download.message())
        } else {
            Txt(detail.download.message(), style = MaterialTheme.typography.bodyMedium, color = muted())
        }
        Field(
            "APKMirror listing URL (optional)", mirror, onMirror, leadingIcon = R.drawable.ms_link, mono = true,
            hint = "The page that lists every version of the app.",
            trailing = {
                IconAction(R.drawable.ms_content_paste, "Paste from clipboard", {
                    scope.launch { clipboard.getClipEntry()?.clipData?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.text?.let { onMirror(it.toString().trim()) } }
                })
            },
        )
        Field("APKPure URL (optional)", pure, onPure, leadingIcon = R.drawable.ms_link, mono = true)
    }
    HeaderRow("After you enable", null) {}
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(start = ScreenPadding, end = LIST_TRAILING_INSET, top = 8.dp, bottom = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(24.dp), contentAlignment = Alignment.Center) { Glyph(R.drawable.ms_build) }
        Txt("Relume builds the app right away, then keeps it updated on the global schedule.", style = MaterialTheme.typography.bodyMedium, color = muted())
    }
}
