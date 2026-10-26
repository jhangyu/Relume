package io.github.jhangyu.relume.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.carousel.HorizontalMultiBrowseCarousel
import androidx.compose.material3.carousel.rememberCarouselState
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.FeedStatus
import io.github.jhangyu.relume.api.MarketApp
import java.util.Locale
import kotlin.math.roundToInt

private val FEATURED_WIDTH = 224.dp
private val FEATURED_HEIGHT = 176.dp
private val FEATURED_RADIUS = 28.dp
private val FEATURED_MEDIUM = 56.dp
private val FEATURED_SMALL = 32.dp
private val FEATURED_PADDING = 8.dp

/** Below this visible width a featured item shows only its tile; below the tile size, nothing (multi-browse medium / small). */
private val FEATURED_FULL_MIN = 120.dp
private val FEATURED_TILE = 48.dp
private val FEATURED_TILE_SMALL = 40.dp


/** Server sort keys in menu order, with their labels (spec 7.3). */
private val SORTS = listOf("bundles" to "Most bundles", "stars" to "Most stars", "updated" to "Recently updated", "name" to "Name A–Z")
private val ORIGINS = listOf(null to "All origins", "official" to "Official catalog", "builtin" to "Built-in", "custom" to "Custom")

/** Every feed or source that failed, with the server's error text as is. */
private fun FeedStatus.errors(): List<String> =
    feeds.mapNotNull { f -> f.lastError?.let { "${f.feed}: $it" } } + sources.mapNotNull { s -> s.lastError?.let { "${s.repo}: $it" } }

/** Marketplace tab: search bar, category chips, featured carousel, "All apps" with sort, two-line app list. Pull down refreshes the catalog. */
@Composable
fun MarketplaceScreen(model: MarketModel, onOpen: (String) -> Unit, onAddSource: () -> Unit) {
    LaunchedEffect(Unit) { if (model.list == null) model.loadAll() }
    val list = model.list
    val unfiltered = model.query.isEmpty() && model.category == null && model.origin == null
    val listState = rememberLazyListState()
    // New results start at the top, so the featured row (first item) is in view again when the filters are cleared.
    LaunchedEffect(model.query, model.category, model.origin, model.sort) { listState.scrollToItem(0) }

    Column(Modifier.fillMaxSize()) {
        AppBar("Marketplace", actions = {
            IconAction(R.drawable.ms_add_link, "Add custom source", onAddSource, IconTone.Link)
            Avatar()
        })
        val listError = model.listLoad.error
        // The list failure has its own banner with Retry, kept until a load succeeds; this one is for every other request.
        model.error?.takeIf { it != listError }?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        listError?.let { Banner("Could not load the Marketplace", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Retry", onAction = model::loadAll) }
        var originMenu by remember { mutableStateOf(false) }
        SearchBar(
            model.query, model::search,
            "Search " + (model.universe?.let { String.format(Locale.ENGLISH, "%,d", it) + " apps" } ?: "apps"),
            Modifier.padding(horizontal = ScreenPadding),
            trailing = {
                Box {
                    IconAction(R.drawable.ms_sort, "Sort and origin filter", { originMenu = true })
                    Menu(originMenu, { originMenu = false }, ORIGINS, model.origin, model::selectOrigin)
                }
            },
        )
        val categories = list?.categories.orEmpty()
        ChipRow(
            listOf("All") + categories.map { "${it.name} ${it.count}" },
            categories.indexOfFirst { it.name == model.category } + 1,
            categories = listOf(null) + categories.map { it.name },
        ) { i -> model.selectCategory(if (i == 0) null else categories[i - 1].name) }
        PullToRefreshBox(isRefreshing = model.refreshing, onRefresh = model::refreshCatalog, modifier = Modifier.weight(1f)) {
            LazyColumn(Modifier.fillMaxSize(), state = listState) {
                if (unfiltered && model.listLoad.emptyCatalog) {
                    item(key = "empty") { EmptyCatalog(checkNotNull(list).feed, model.refreshing, model::refreshCatalog) }
                }
                val featured = model.featured
                if (unfiltered && featured == null) {
                    // Reserve the slot while the featured requests run, so the list does not open scrolled past it.
                    item(key = "featured") { Spacer(Modifier.height(FEATURED_HEIGHT + FEATURED_PADDING * 2)) }
                } else if (unfiltered && !featured.isNullOrEmpty()) {
                    item(key = "featured") { Featured(featured, model, onOpen) }
                }
                if (list == null && listError == null) {
                    item(key = "loading") { Txt("Loading…", Modifier.padding(ScreenPadding), color = muted()) }
                }
                if (list != null && list.total > 0) item(key = "header") { AllAppsHeader(model) }
                items(list?.apps.orEmpty(), key = { it.packageName }) { app -> AppRow(app, model, onOpen) }
                if (list != null && list.hasMore) {
                    // Composed when the user scrolls near the end: fetch the next page, once per loaded list.
                    item(key = "more") {
                        LaunchedEffect(list) { model.loadMore() }
                        Txt("Loading more…", Modifier.padding(ScreenPadding), color = muted())
                    }
                }
                if (list != null && list.apps.isEmpty() && (!unfiltered || list.total > 0)) {
                    item(key = "none") { Txt("No apps match.", Modifier.padding(ScreenPadding), color = muted()) }
                }
            }
        }
    }
}

/** Dropdown of [options] (key to label); the current key is marked with a check. */
@Composable
private fun <K> Menu(open: Boolean, onClose: () -> Unit, options: List<Pair<K, String>>, current: K, onPick: (K) -> Unit) {
    DropdownMenu(open, onClose, containerColor = MaterialTheme.colorScheme.surfaceContainer) {
        options.forEach { (key, label) ->
            DropdownMenuItem(
                text = { Text(label, style = MaterialTheme.typography.bodyLarge) },
                onClick = { onClose(); onPick(key) },
                trailingIcon = if (key == current) ({ Glyph(R.drawable.ms_check, MaterialTheme.colorScheme.secondary) }) else null,
            )
        }
    }
}

/** Section header "All apps" with the sort menu; the text button is pulled out 12 dp so its label ends on the content edge. */
@Composable
private fun AllAppsHeader(model: MarketModel) {
    var open by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().heightIn(min = TouchTarget).padding(start = ScreenPadding, end = 4.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f)) { SectionHeader("All apps") }
        Box {
            ActionButton(SORTS.first { it.first == model.sort }.second, { open = true }, kind = ButtonKind.Text, icon = R.drawable.ms_sort)
            Menu(open, { open = false }, SORTS, model.sort, model::selectSort)
        }
    }
}

@Composable
private fun AppRow(app: MarketApp, model: MarketModel, onOpen: (String) -> Unit) {
    ListRow(
        app.name,
        Modifier.clickable { onOpen(app.packageName) },
        supportingContent = {
            CategoryTag(app.category, height = ListPillHeight)
            Text(plural(app.bundleCount, "bundle"), maxLines = 1)
        },
        leading = { AppTile(app.name, app.iconUrl, model.api, category = app.category) },
        trailing = {
            if (app.enabled) Pill("On", MaterialTheme.relume.green.container, MaterialTheme.relume.green.onContainer, R.drawable.ms_check, ListPillHeight)
            else Glyph(R.drawable.ms_chevron_right)
        },
    )
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

/**
 * MD3 multi-browse carousel (large 224 x 176 dp, medium 56, small 32): category gradient, uppercase label, app tile on white,
 * name and stats. Narrow items show only the tile, centred in what is visible.
 */
// Opt-in: HorizontalMultiBrowseCarousel is still @ExperimentalMaterial3Api in material3 1.4.0 (BOM 2026.09.00); the spec asks for this carousel (8.3).
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Featured(apps: List<MarketApp>, model: MarketModel, onOpen: (String) -> Unit) {
    val density = LocalDensity.current
    HorizontalMultiBrowseCarousel(
        state = rememberCarouselState { apps.size },
        preferredItemWidth = FEATURED_WIDTH,
        modifier = Modifier.fillMaxWidth().padding(vertical = FEATURED_PADDING).height(FEATURED_HEIGHT),
        itemSpacing = 8.dp,
        minSmallItemWidth = FEATURED_SMALL,
        maxSmallItemWidth = FEATURED_MEDIUM,
        contentPadding = PaddingValues(horizontal = ScreenPadding),
    ) { i ->
        val app = apps[i]
        val (first, second) = categoryPastels(app.category)
        val mask = carouselItemDrawInfo.maskRect
        val visible = with(density) { mask.width.toDp() }
        Box(
            Modifier.fillMaxSize().maskClip(RoundedCornerShape(FEATURED_RADIUS))
                .background(Brush.linearGradient(listOf(first.container, second.container)))
                .clickable { onOpen(app.packageName) },
        ) {
            Box(Modifier.offset { IntOffset(mask.left.roundToInt(), 0) }.width(visible).fillMaxHeight(), contentAlignment = Alignment.Center) {
                when {
                    visible >= FEATURED_FULL_MIN -> Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                            Txt("FEATURED", style = MaterialTheme.typography.labelMedium, color = first.onContainer)
                            AppTile(app.name, app.iconUrl, model.api, size = FEATURED_TILE, category = app.category, onWhite = true)
                        }
                        Column {
                            Txt(app.name, style = MaterialTheme.typography.titleLarge, color = first.onContainer, maxLines = 1)
                            Txt(
                                plural(app.bundleCount, "bundle") + (app.stars?.let { " · $it ★" } ?: ""),
                                style = MaterialTheme.typography.bodySmall, color = first.onContainer, maxLines = 1,
                            )
                        }
                    }
                    visible >= FEATURED_TILE_SMALL -> AppTile(app.name, app.iconUrl, model.api, size = FEATURED_TILE_SMALL, category = app.category, onWhite = true)
                }
            }
        }
    }
}
