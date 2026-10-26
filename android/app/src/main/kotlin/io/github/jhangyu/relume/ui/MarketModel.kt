package io.github.jhangyu.relume.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewModelScope
import io.github.jhangyu.relume.api.MarketDetail
import io.github.jhangyu.relume.api.MarketList
import kotlinx.coroutines.launch

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
    val api get() = host.api

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
        host.viewModelScope.launch { detail = host.call { it.marketApp(pkg) } }
    }

    /** Enable [pkg] with [bundleId]; [done] gets the new app id. */
    fun enable(pkg: String, bundleId: Int, done: (Int) -> Unit) {
        host.viewModelScope.launch {
            val app = host.call { it.enable(pkg, bundleId, null, null) } ?: return@launch
            host.refresh()
            load()
            done(app.id)
        }
    }

    fun openAddSource() {
        addSourceOpen = true
    }

    fun closeAddSource() {
        addSourceOpen = false
    }

    fun addSource(url: String, engine: String, assetRegex: String?, packages: List<String>?) {
        addSourceOpen = false
        host.viewModelScope.launch {
            host.call { it.addSource(url, engine, assetRegex, packages) } ?: return@launch
            load()
        }
    }

    fun reset() {
        query = ""
        category = null
        list = null
        detail = null
        addSourceOpen = false
    }
}
