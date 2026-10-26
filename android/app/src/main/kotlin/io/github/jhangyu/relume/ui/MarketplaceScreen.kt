package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.FeedStatus

private val ENGINES = listOf("morphe", "lspatch", "npatch")

private fun FeedStatus.headline(total: Int): String {
    val official = feeds.filter { it.origin == "official" }.sumOf { it.bundleCount }
    val custom = sources.count { it.origin == "custom" }
    return "$total apps · $official community bundles · built-in · $custom custom"
}

/** Marketplace tab (thin first version): search, category chips, app list with tile and bundle count. */
@Composable
fun MarketplaceScreen(model: MarketModel, onOpen: (String) -> Unit, onAddSource: () -> Unit) {
    LaunchedEffect(Unit) { if (model.list == null) model.load() }
    val list = model.list
    val categories = list?.categories.orEmpty()
    val selected = categories.indexOfFirst { it.name == model.category } + 1

    Column(Modifier.fillMaxSize()) {
        AppBar("Marketplace", actions = {
            IconAction(R.drawable.ms_add_link, "Add custom source", onAddSource, IconTone.Link)
            Avatar()
        })
        Field(
            "Search apps", model.query, model::search,
            Modifier.padding(horizontal = ScreenPadding), leadingIcon = R.drawable.ms_search,
        )
        ChipRow(listOf("All") + categories.map { "${it.name} ${it.count}" }, selected) { i ->
            model.selectCategory(if (i == 0) null else categories[i - 1].name)
        }
        if (list != null) Txt(list.feed.headline(list.total), Modifier.padding(horizontal = ScreenPadding), style = MaterialTheme.typography.bodySmall, color = muted())
        LazyColumn(Modifier.weight(1f).fillMaxSize()) {
            items(list?.apps.orEmpty(), key = { it.packageName }) { app ->
                ListRow(
                    app.name,
                    Modifier.clickable { onOpen(app.packageName) },
                    supporting = "${app.bundleCount} bundles" + (app.category?.let { " · $it" } ?: ""),
                    leading = { AppTile(app.name, app.iconUrl, model.api) },
                    trailing = {
                        if (app.enabled) Pill("Enabled", MaterialTheme.relume.green.container, MaterialTheme.relume.green.onContainer, R.drawable.ms_check)
                        else Glyph(R.drawable.ms_chevron_right)
                    },
                )
            }
            if (list != null && list.apps.isEmpty()) {
                item { Txt("No apps match.", Modifier.padding(ScreenPadding), color = muted()) }
            }
        }
    }
}

/** Dialog behind the top-bar "Add custom source" button (URL, engine, module options). */
@Composable
fun AddSourceDialog(model: MarketModel) {
    if (!model.addSourceOpen) return
    var url by remember { mutableStateOf("") }
    var engine by remember { mutableStateOf(ENGINES.first()) }
    var regex by remember { mutableStateOf("") }
    var packages by remember { mutableStateOf("") }
    val module = engine != ENGINES.first()
    RDialog(model::closeAddSource) {
        Txt("Add custom source", style = MaterialTheme.typography.headlineSmall)
        Field("GitHub repository URL", url, { url = it }, leadingIcon = R.drawable.ms_link)
        ChipRow(ENGINES, ENGINES.indexOf(engine)) { engine = ENGINES[it] }
        if (module) {
            Field("Asset regex", regex, { regex = it }, mono = true)
            Field("Target packages (comma separated)", packages, { packages = it }, mono = true)
        }
        DialogButtons {
            ActionButton("Cancel", model::closeAddSource, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton(
                "Add",
                { model.addSource(url.trim(), engine, regex.trim().takeIf { module && it.isNotEmpty() }, packages.split(',').map { it.trim() }.filter { it.isNotEmpty() }.takeIf { module && it.isNotEmpty() }) },
                Modifier.weight(1f),
                enabled = url.isNotBlank(),
            )
        }
    }
}
