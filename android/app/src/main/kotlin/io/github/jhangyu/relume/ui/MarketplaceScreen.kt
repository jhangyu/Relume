package io.github.jhangyu.relume.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.FeedStatus
import io.github.jhangyu.relume.api.MarketApp

private val FEATURED_WIDTH = 200.dp
private val FEATURED_HEIGHT = 112.dp
private val FEATURED_RADIUS = 28.dp

private fun FeedStatus.headline(total: Int): String {
    val official = feeds.filter { it.origin == "official" }.sumOf { it.bundleCount }
    val custom = sources.count { it.origin == "custom" }
    return "$total apps · $official community bundles · built-in · $custom custom"
}

/** Every feed or source that failed, with the server's error text as is. */
private fun FeedStatus.errors(): List<String> =
    feeds.mapNotNull { f -> f.lastError?.let { "${f.feed}: $it" } } + sources.mapNotNull { s -> s.lastError?.let { "${s.repo}: $it" } }

/** Marketplace tab: search, category chips, featured carousel, two-line app list. */
@Composable
fun MarketplaceScreen(model: MarketModel, onOpen: (String) -> Unit, onAddSource: () -> Unit) {
    LaunchedEffect(Unit) { if (model.list == null) model.loadAll() }
    val list = model.list
    val categories = list?.categories.orEmpty()
    val selected = categories.indexOfFirst { it.name == model.category } + 1
    val unfiltered = model.query.isEmpty() && model.category == null

    Column(Modifier.fillMaxSize()) {
        AppBar("Marketplace", actions = {
            IconAction(R.drawable.ms_sync, "Refresh catalog", model::refreshCatalog, IconTone.Sync, enabled = !model.refreshing)
            IconAction(R.drawable.ms_add_link, "Add custom source", onAddSource, IconTone.Link)
            Avatar()
        })
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        Field(
            "Search apps", model.query, model::search,
            Modifier.padding(horizontal = ScreenPadding), leadingIcon = R.drawable.ms_search,
        )
        ChipRow(listOf("All") + categories.map { "${it.name} ${it.count}" }, selected) { i ->
            model.selectCategory(if (i == 0) null else categories[i - 1].name)
        }
        if (list != null) Txt(list.feed.headline(list.total), Modifier.padding(horizontal = ScreenPadding), style = MaterialTheme.typography.bodySmall, color = muted())
        LazyColumn(Modifier.weight(1f).fillMaxSize()) {
            if (list != null && unfiltered && list.total == 0) {
                item { EmptyCatalog(list.feed, model.refreshing, model::refreshCatalog) }
            }
            if (list != null && unfiltered) {
                if (model.featured.isNotEmpty()) item { Featured(model.featured, onOpen) }
            }
            items(list?.apps.orEmpty(), key = { it.packageName }) { app ->
                ListRow(
                    app.name,
                    Modifier.clickable { onOpen(app.packageName) },
                    supporting = "${app.bundleCount} bundles" + (app.category?.let { " · $it" } ?: ""),
                    leading = { AppTile(app.name, app.iconUrl, model.api) },
                    trailing = {
                        if (app.enabled) Pill("On", MaterialTheme.relume.green.container, MaterialTheme.relume.green.onContainer, R.drawable.ms_check)
                        else Glyph(R.drawable.ms_chevron_right)
                    },
                )
            }
            if (list != null && list.hasMore) {
                // Composed when the user scrolls near the end: fetch the next page, once per loaded list.
                item(key = "more") {
                    LaunchedEffect(list) { model.loadMore() }
                    Txt("Loading more…", Modifier.padding(ScreenPadding), color = muted())
                }
            }
            if (list != null && list.apps.isEmpty() && (!unfiltered || list.total > 0)) {
                item { Txt("No apps match.", Modifier.padding(ScreenPadding), color = muted()) }
            }
        }
    }
}

/** Shown before the first catalog refresh: never a silent blank. */
@Composable
private fun EmptyCatalog(feed: FeedStatus, refreshing: Boolean, onRefresh: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(ScreenPadding), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Txt("The Marketplace is empty", style = MaterialTheme.typography.titleLarge)
        Txt("No catalog has been loaded yet. Refresh the catalog to list apps and patch bundles.", color = muted())
        feed.errors().forEach { Banner("Feed failed", Tone.Danger, detail = it) }
        ActionButton(if (refreshing) "Refreshing…" else "Refresh catalog", onRefresh, Modifier.fillMaxWidth(), enabled = !refreshing, icon = R.drawable.ms_sync)
    }
}

// ponytail: plain LazyRow of fixed-width cards, not HorizontalMultiBrowseCarousel (still @ExperimentalMaterial3Api). Swap when it is stable.
@Composable
private fun Featured(apps: List<MarketApp>, onOpen: (String) -> Unit) {
    val r = MaterialTheme.relume
    val hues = listOf(r.lavender, r.blue, r.teal, r.amber)
    LazyRow(contentPadding = PaddingValues(horizontal = ScreenPadding, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(apps.size, key = { apps[it].packageName }) { i ->
            val app = apps[i]
            val hue = hues[i % hues.size]
            Surface(
                Modifier.width(FEATURED_WIDTH).height(FEATURED_HEIGHT).clickable { onOpen(app.packageName) },
                shape = RoundedCornerShape(FEATURED_RADIUS),
                color = hue.container,
                contentColor = hue.onContainer,
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                    Txt("Featured", style = MaterialTheme.typography.labelMedium, color = hue.onContainer)
                    Column {
                        Txt(app.name, style = MaterialTheme.typography.titleLarge, color = hue.onContainer, maxLines = 1)
                        Txt(
                            "${app.bundleCount} bundles" + (app.stars?.let { " · $it ★" } ?: ""),
                            style = MaterialTheme.typography.bodySmall, color = hue.onContainer, maxLines = 1,
                        )
                    }
                }
            }
        }
    }
}
