package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.MarketApp
import org.junit.Assert.assertEquals
import org.junit.Test

class MarketFilterTest {
    private fun app(pkg: String, bundles: Int, updated: String?, vararg origins: String) =
        MarketApp(pkg, pkg, null, null, bundles, null, origins.toList(), updated, null, false)

    @Test
    fun featuredIsTopByBundlesThenBuiltinThenRecentWithoutDuplicates() {
        val apps = listOf(
            app("a", 9, "2026-01-01", "official"),
            app("b", 8, "2026-01-02", "official"),
            app("c", 7, null, "official"),
            app("d", 1, "2026-01-03", "builtin", "official"),
            app("e", 2, "2026-03-01", "official"),
            app("f", 1, "2026-02-01", "official"),
            app("g", 1, "2026-04-01", "official"),
            app("h", 1, "2025-01-01", "official"),
        )
        // top3 bundles: a b c | builtin: d | recent 3: g e f
        assertEquals(listOf("a", "b", "c", "d", "g", "e", "f"), featuredApps(apps).map { it.packageName })
    }

    @Test
    fun featuredOfNothingIsEmpty() {
        assertEquals(emptyList<MarketApp>(), featuredApps(emptyList()))
    }
}
