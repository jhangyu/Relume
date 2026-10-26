package io.github.jhangyu.relume.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewModelScope
import io.github.jhangyu.relume.api.MarketApp
import io.github.jhangyu.relume.api.MarketDetail
import io.github.jhangyu.relume.api.MarketList
import io.github.jhangyu.relume.api.RelumeApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private const val FEATURED_TOP = 3
private const val FEATURED_RECENT = 3
private const val DEFAULT_SORT = "bundles"

/** One marketplace request (origin, sort, page size); null when it failed and the error is already shown. */
typealias FeaturedFetch = suspend (origin: String?, sort: String, perPage: Int) -> MarketList?

/**
 * Spec 2.7 over the whole catalog, by server-side sort: top 3 by bundle count, the built-in apps, the 3 most
 * recently updated; each app once, in that order. Null when a request failed.
 */
// ponytail: built-in apps capped at one page (RelumeApi.MARKET_PAGE_SIZE), page them if a built-in list ever grows past it
suspend fun featuredApps(fetch: FeaturedFetch): List<MarketApp>? {
    val top = fetch(null, "bundles", FEATURED_TOP) ?: return null
    val builtin = fetch("builtin", "bundles", RelumeApi.MARKET_PAGE_SIZE) ?: return null
    val recent = fetch(null, "updated", FEATURED_RECENT) ?: return null
    return (top.apps + builtin.apps + recent.apps.filter { it.updatedAt != null }).distinctBy { it.packageName }
}

/** "1 bundle", "2 bundles"; [many] for irregular plurals ("patches"). */
fun plural(n: Int, one: String, many: String = one + "s"): String = "$n " + if (n == 1) one else many

/** "oldest – newest" of a bundle's versions (the server sends them newest first); null when empty (any version). */
fun versionRange(newestFirst: List<String>): String? = when (newestFirst.size) {
    0 -> null
    1 -> newestFirst.single()
    else -> "${newestFirst.last()} – ${newestFirst.first()}"
}

/** True while pages after [MarketList.page] exist on the server. */
val MarketList.hasMore get() = page * perPage < total

/** [list] with the next page appended, or null when there is none or the request failed. */
suspend fun nextMarketPage(list: MarketList, fetch: suspend (page: Int) -> MarketList?): MarketList? {
    if (!list.hasMore) return null
    val next = fetch(list.page + 1) ?: return null
    return next.copy(apps = (list.apps + next.apps).distinctBy { it.packageName })
}

/** The shown page-1 list and why the last page-1 load failed; [error] stays until a load succeeds. */
data class ListState(val list: MarketList? = null, val error: String? = null) {
    /** The server answered with an empty catalog; never true before a load succeeded. */
    val emptyCatalog get() = list != null && list.total == 0
}

/**
 * One page-1 load: a page replaces the list and clears the error. A failure keeps the shown list and records [failure]
 * (null when the request was not sent, e.g. signed out: the old error stays), so a failed first load is never a silent empty list.
 */
suspend fun ListState.reload(fetch: suspend () -> MarketList?, failure: () -> String?): ListState {
    val page = fetch() ?: return copy(error = failure() ?: error)
    return ListState(page)
}

/** Marketplace list, its filters and the selected app detail. Server calls and 401 handling go through [host]. */
class MarketModel(private val host: AppModel) {
    var query by mutableStateOf("")
        private set
    var category by mutableStateOf<String?>(null)
        private set

    /** Server sort key of the list: bundles (default, as on the web), stars, updated or name. */
    var sort by mutableStateOf(DEFAULT_SORT)
        private set

    /** Origin filter of the list: null (all), official, builtin or custom. */
    var origin by mutableStateOf<String?>(null)
        private set
    var listLoad by mutableStateOf(ListState())
        private set
    val list get() = listLoad.list

    /** App count of the whole catalog (the last unfiltered page 1), for the search placeholder. */
    var universe by mutableStateOf<Int?>(null)
        private set
    /** Featured row; null until its requests finished (the list keeps its slot), empty when there is none. */
    var featured by mutableStateOf<List<MarketApp>?>(null)
        private set
    var detail by mutableStateOf<MarketDetail?>(null)
        private set
    var detailMissing by mutableStateOf(false)
        private set
    var refreshing by mutableStateOf(false)
        private set

    /** Patch switches the user changed, by name; only these are sent to the server. */
    var patchChanges by mutableStateOf<Map<String, Boolean>>(emptyMap())
        private set
    val api get() = host.api
    val error get() = host.error

    fun dismissError() = host.dismissError()

    /** Pull every feed now. A failed feed stays visible in [list]`.feed`; a request failure lands in [error]. */
    fun refreshCatalog() {
        host.viewModelScope.launch {
            refreshing = true
            try {
                host.call { it.refreshCatalog() } ?: return@launch
                loadAll()
            } finally {
                refreshing = false
            }
        }
    }

    fun setPatch(name: String, value: Boolean, default: Boolean?) {
        patchChanges = if (value == default) patchChanges - name else patchChanges + (name to value)
    }

    fun clearPatchChanges() {
        patchChanges = emptyMap()
    }

    var addSourceOpen by mutableStateOf(false)
        private set

    private var listJob: Job? = null

    fun search(q: String) {
        query = q
        load()
    }

    fun selectCategory(c: String?) {
        category = c
        load()
    }

    fun selectSort(key: String) {
        sort = key
        load()
    }

    fun selectOrigin(o: String?) {
        origin = o
        load()
    }

    /** The list (page 1) and the featured row: on first show and after the catalog changed. */
    fun loadAll() {
        load()
        host.viewModelScope.launch {
            // A failed request is shown in [error]; the row then stays empty instead of holding its slot.
            featured = featuredApps { origin, sort, perPage ->
                host.call { it.marketplace(null, null, origin, sort, null, perPage) }
            }.orEmpty()
        }
    }

    /** Page 1 for the current query and category; cancels a load or page request still running. */
    fun load() {
        listJob?.cancel()
        listJob = host.viewModelScope.launch {
            listLoad = listLoad.reload({ host.call { it.marketplace(query, category, origin, sort, null) } }, { host.error })
            val page = listLoad.list
            if (listLoad.error == null && page != null && query.isBlank() && category == null && origin == null) universe = page.total
        }
    }

    /** Append the next page; no-op while a list request runs or when every page is loaded. */
    fun loadMore() {
        val current = list ?: return
        if (listJob?.isActive == true || !current.hasMore) return
        listJob = host.viewModelScope.launch {
            val more = nextMarketPage(current) { p -> host.call { it.marketplace(query, category, origin, sort, p) } } ?: return@launch
            listLoad = listLoad.copy(list = more)
        }
    }

    fun open(pkg: String) {
        detail = null
        detailMissing = false
        patchChanges = emptyMap()
        host.viewModelScope.launch {
            detail = host.call { it.marketApp(pkg) }
            detailMissing = detail == null
        }
    }

    /** Enable [pkg] with [bundleId], the changed patch switches and the optional URLs; [done] gets the new app id. */
    fun enable(pkg: String, bundleId: Int, apkmirrorListUrl: String, apkpureUrl: String, done: (Int) -> Unit) {
        val download: JsonObject? = buildJsonObject {
            if (apkmirrorListUrl.isNotBlank()) put("apkmirror_list_url", apkmirrorListUrl.trim())
            if (apkpureUrl.isNotBlank()) put("apkpure_url", apkpureUrl.trim())
        }.takeIf { it.isNotEmpty() }
        host.viewModelScope.launch {
            val app = host.call { it.enable(pkg, bundleId, patchChanges.takeIf { c -> c.isNotEmpty() }, download) } ?: return@launch
            host.refresh()
            loadAll()
            done(app.id)
        }
    }

    fun openAddSource() {
        host.dismissError()
        addSourceOpen = true
    }

    fun closeAddSource() {
        addSourceOpen = false
    }

    fun addSource(url: String, engine: String, assetRegex: String?, packages: List<String>?) {
        host.viewModelScope.launch {
            host.call { it.addSource(url, engine, assetRegex, packages) } ?: return@launch
            addSourceOpen = false
            loadAll()
        }
    }

    fun reset() {
        query = ""
        category = null
        sort = DEFAULT_SORT
        origin = null
        listJob?.cancel()
        listLoad = ListState()
        universe = null
        featured = null
        detail = null
        detailMissing = false
        patchChanges = emptyMap()
        addSourceOpen = false
    }
}
