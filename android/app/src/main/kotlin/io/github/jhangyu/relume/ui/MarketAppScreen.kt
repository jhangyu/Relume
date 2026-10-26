package io.github.jhangyu.relume.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.BundleOption
import io.github.jhangyu.relume.api.DownloadState

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

/** App detail from the Marketplace: bundle radio list, patch switches, then the download-source step with "Enable and build". */
@Composable
fun MarketAppScreen(model: MarketModel, pkg: String, onBack: () -> Unit, onEnabled: (Int) -> Unit) {
    LaunchedEffect(pkg) { model.open(pkg) }
    val detail = model.detail?.takeIf { it.packageName == pkg }
    var chosen by remember(pkg) { mutableStateOf<Int?>(null) }
    var downloadStep by remember(pkg) { mutableStateOf(false) }
    var mirrorUrl by remember(pkg) { mutableStateOf("") }
    var pureUrl by remember(pkg) { mutableStateOf("") }
    val bundle = detail?.bundles?.firstOrNull { it.bundleId == chosen }

    Column(Modifier.fillMaxSize()) {
        AppBar(detail?.name ?: pkg, onBack = if (downloadStep) ({ downloadStep = false }) else onBack)
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        if (detail == null) {
            Txt(
                if (model.detailMissing) "This app is in no catalog yet. Refresh the catalog in the Marketplace tab." else "Loading…",
                Modifier.padding(ScreenPadding), color = muted(),
            )
            return@Column
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                AppTile(detail.name, detail.iconUrl, model.api, size = 64.dp)
                Column {
                    Txt(detail.name, style = MaterialTheme.typography.headlineSmall)
                    Txt(detail.packageName, style = Mono, color = muted(), maxLines = 1)
                    CategoryTag(detail.category)
                }
            }
            if (downloadStep) {
                DownloadStep(detail.download, mirrorUrl, { mirrorUrl = it }, pureUrl, { pureUrl = it })
            } else {
                detail.description?.let { Txt(it, style = MaterialTheme.typography.bodyMedium, color = muted()) }
                SectionHeader("Patch bundle")
                InsetGroup {
                    detail.bundles.forEach { b ->
                        RadioRow(selected = chosen == b.bundleId, enabled = b.selectable, onSelect = { chosen = b.bundleId; model.clearPatchChanges() }) {
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                Txt(b.name)
                                Txt(
                                    b.notSelectableReason ?: "${b.patches.size} patches" + (b.stars?.let { " · $it stars" } ?: ""),
                                    style = MaterialTheme.typography.bodyMedium, color = muted(),
                                )
                            }
                            OriginLabel(b.origin)
                        }
                    }
                }
                if (bundle != null) PatchList(model, bundle)
            }
        }
        Column(Modifier.fillMaxWidth().padding(ScreenPadding)) {
            if (detail.enabled) {
                Txt("Enabled", color = muted())
            } else if (!downloadStep) {
                ActionButton("Next: download source", { downloadStep = true }, Modifier.fillMaxWidth(), enabled = bundle != null)
            } else {
                // Enable depends only on the bundle choice: the URLs are optional (spec 12 item 7).
                ActionButton("Enable and build", { model.enable(pkg, checkNotNull(chosen), mirrorUrl, pureUrl, onEnabled) }, Modifier.fillMaxWidth(), enabled = chosen != null)
            }
        }
    }
}

@Composable
private fun PatchList(model: MarketModel, bundle: BundleOption) {
    SectionHeader("Patches")
    InsetGroup {
        bundle.patches.forEach { p ->
            val changed = p.name in model.patchChanges
            val on = model.patchChanges[p.name] ?: p.defaultEnabled ?: false
            SwitchRow(
                p.name,
                listOfNotNull(p.description, if (p.defaultEnabled == null) "default unknown" else null).joinToString(" · ").ifEmpty { null },
                on,
            ) { model.setPatch(p.name, it, p.defaultEnabled) }
            if (changed) Pill("Changed by you", MaterialTheme.relume.amber.container, MaterialTheme.relume.amber.onContainer)
        }
    }
}

@Composable
private fun DownloadStep(download: DownloadState?, mirror: String, onMirror: (String) -> Unit, pure: String, onPure: (String) -> Unit) {
    SectionHeader("Download source")
    Txt(download.message(), color = muted())
    Txt("Both links are optional. You can add them later in the app settings.", style = MaterialTheme.typography.bodyMedium, color = muted())
    Field("APKMirror listing URL", mirror, onMirror, leadingIcon = R.drawable.ms_link, mono = true)
    Field("APKPure URL", pure, onPure, leadingIcon = R.drawable.ms_link, mono = true)
}
