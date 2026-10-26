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

/** App detail from the Marketplace (thin first version): bundle radio list and "Enable and build". */
@Composable
fun MarketAppScreen(model: MarketModel, pkg: String, onBack: () -> Unit, onEnabled: (Int) -> Unit) {
    LaunchedEffect(pkg) { model.open(pkg) }
    val detail = model.detail?.takeIf { it.packageName == pkg }
    var chosen by remember(pkg) { mutableStateOf<Int?>(null) }

    Column(Modifier.fillMaxSize()) {
        AppBar(detail?.name ?: pkg, onBack = onBack)
        if (detail == null) {
            Txt("Loading…", Modifier.padding(ScreenPadding), color = muted())
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
            detail.description?.let { Txt(it, style = MaterialTheme.typography.bodyMedium, color = muted()) }
            SectionHeader("Patch bundle")
            InsetGroup {
                detail.bundles.forEach { b ->
                    RadioRow(selected = chosen == b.bundleId, enabled = b.selectable, onSelect = { chosen = b.bundleId }) {
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
        }
        val id = chosen
        Column(Modifier.fillMaxWidth().padding(ScreenPadding)) {
            if (detail.enabled) {
                Txt("Enabled", color = muted())
            } else {
                ActionButton("Enable and build", { model.enable(pkg, checkNotNull(id), onEnabled) }, Modifier.fillMaxWidth(), enabled = id != null)
            }
        }
    }
}
