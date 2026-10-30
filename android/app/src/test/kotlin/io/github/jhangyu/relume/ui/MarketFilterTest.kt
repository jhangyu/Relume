package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.MarketApp
import io.github.jhangyu.relume.api.MarketList
import io.github.jhangyu.relume.api.RelumeApi
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MarketFilterTest {
    private fun app(pkg: String, updated: String? = "2026-01-01") =
        MarketApp(pkg, pkg, null, null, 1, null, listOf("official"), updated, null, false)

    private fun page(n: Int, perPage: Int, total: Int, vararg pkgs: String) =
        MarketList(total, n, perPage, emptyList(), pkgs.map { app(it) })

    @Test
    fun pagingRequestsPage2AfterPage1AndStopsAtTotal() = runBlocking {
        val requested = mutableListOf<Int>()
        val pages = mapOf(2 to page(2, 2, 5, "c", "d"), 3 to page(3, 2, 5, "e"))
        val fetch: suspend (Int) -> MarketList? = { p -> requested += p; pages.getValue(p) }
        val p2 = nextMarketPage(page(1, 2, 5, "a", "b"), fetch)!!
        assertEquals(listOf("a", "b", "c", "d"), p2.apps.map { it.packageName })
        val p3 = nextMarketPage(p2, fetch)!!
        assertEquals(listOf("a", "b", "c", "d", "e"), p3.apps.map { it.packageName })
        assertNull(nextMarketPage(p3, fetch))
        assertEquals(listOf(2, 3), requested)
    }

    @Test
    fun failedPageKeepsNothing() = runBlocking {
        assertNull(nextMarketPage(page(1, 2, 5, "a", "b")) { null })
    }

    @Test
    fun featuredComesFromServerSortsNotFromTheListPage() = runBlocking {
        val requests = mutableListOf<Triple<String?, String, Int>>()
        val answers = mapOf(
            Triple(null, "bundles", 3) to listOf(app("top1"), app("top2"), app("both")),
            Triple("builtin", "bundles", RelumeApi.MARKET_PAGE_SIZE) to listOf(app("builtin"), app("both")),
            Triple(null, "updated", 3) to listOf(app("new1"), app("top1"), app("never", updated = null)),
        )
        val featured = featuredApps { origin, sort, perPage ->
            val key = Triple(origin, sort, perPage)
            requests += key
            MarketList(99, 1, perPage, emptyList(), answers.getValue(key))
        }
        assertEquals(answers.keys.toList(), requests)
        // top 3 by bundles | built-in | recent; each app once, never-updated apps are not "recent"
        assertEquals(listOf("top1", "top2", "both", "builtin", "new1"), featured!!.map { it.packageName })
    }

    @Test
    fun featuredIsNullWhenARequestFails() = runBlocking {
        assertNull(featuredApps { _, _, _ -> null })
    }

    @Test
    fun versionRangeIsOldestToNewestOfANewestFirstList() {
        assertEquals("1.12.10 – 1.12.12", versionRange(listOf("1.12.12", "1.12.11", "1.12.10")))
        assertEquals("2.0", versionRange(listOf("2.0")))
        assertNull(versionRange(emptyList()))
    }

    @Test
    fun failedFirstLoadKeepsItsErrorUntilALoadSucceeds() = runBlocking {
        val failed = ListState().reload({ null }, { "HTTP 502 /api/marketplace: bad gateway" })
        assertEquals("HTTP 502 /api/marketplace: bad gateway", failed.error)
        assertNull(failed.list)
        assertEquals(false, failed.emptyCatalog)
        // A request that was not sent (no failure text) does not clear the recorded error.
        assertEquals(failed.error, failed.reload({ null }, { null }).error)
        val ok = failed.reload({ page(1, 2, 0) }, { null })
        assertNull(ok.error)
        assertEquals(true, ok.emptyCatalog)
    }
}
