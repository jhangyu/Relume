package io.github.jhangyu.relume.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewModelScope
import io.github.jhangyu.relume.api.MarketApp
import io.github.jhangyu.relume.api.MarketDetail
import io.github.jhangyu.relume.api.MarketList
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private const val FEATURED_TOP = 3
private const val FEATURED_RECENT = 3

/** Spec 2.7: top 3 by bundle count, the built-in apps, the 3 most recently updated; each app once, in that order. */
fun featuredApps(apps: List<MarketApp>): List<MarketApp> =
    (apps.sortedByDescending { it.bundleCount }.take(FEATURED_TOP) +
        apps.filter { "builtin" in it.origins } +
        apps.filter { it.updatedAt != null }.sortedByDescending { it.updatedAt }.take(FEATURED_RECENT))
        .distinctBy { it.packageName }

/** Marketplace list, its filters and the selected app detail. Server calls and 401 handling go through [host]. */
class MarketModel(private val host: AppModel) {
    var query by mutableStateOf("")
        private set
    var category by mutableStateOf<String?>(null)
        private set
    var list by mutableStateOf<MarketList?>(null)
        private set
    var loading by mutableStateOf(false)
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
                load()
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

    fun search(q: String) {
        query = q
        load()
    }

    fun selectCategory(c: String?) {
        category = c
        load()
    }

    fun load() {
        host.viewModelScope.launch {
            loading = true
            try {
                list = host.call { it.marketplace(query, category, null, null, null) } ?: return@launch
            } finally {
                loading = false
            }
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
            load()
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
            load()
        }
    }

    fun reset() {
        query = ""
        category = null
        list = null
        detail = null
        detailMissing = false
        patchChanges = emptyMap()
        addSourceOpen = false
    }
}
