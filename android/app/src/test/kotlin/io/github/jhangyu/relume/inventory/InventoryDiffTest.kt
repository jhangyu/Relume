package io.github.jhangyu.relume.inventory

import io.github.jhangyu.relume.api.InventoryApp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class InventoryDiffTest {
    private fun app(pkg: String, code: Long = 1, cache: Long? = 3) = InventoryApp(
        packageName = pkg, label = pkg, versionCode = code, versionName = "$code",
        appBytes = cache?.let { 1 }, dataBytes = cache?.let { 2 }, cacheBytes = cache,
        sizeReason = if (cache == null) AppInventory.NO_USAGE_ACCESS else null, iconSha256 = null,
    )

    @Test fun firstReportIsFull() {
        val r = InventoryDiff.report(null, listOf(app("a"), app("b")))
        assertTrue(r.full)
        assertNull(r.baseRev)
        assertEquals(listOf("a", "b"), r.apps.map { it.packageName })
        assertEquals(emptyList<String>(), r.removed)
    }

    @Test fun unchangedIsEmptyDiffOnTheStoredRev() {
        val apps = listOf(app("a"), app("b"))
        val r = InventoryDiff.report(InventorySnapshot(4, apps), apps)
        assertFalse(r.full)
        assertEquals(4L, r.baseRev)
        assertEquals(emptyList<InventoryApp>(), r.apps)
        assertEquals(emptyList<String>(), r.removed)
    }

    @Test fun changedAddedAndRemoved() {
        val before = InventorySnapshot(7, listOf(app("a"), app("b"), app("c")))
        val r = InventoryDiff.report(before, listOf(app("a", code = 2), app("c", cache = null), app("d")))
        assertFalse(r.full)
        assertEquals(7L, r.baseRev)
        assertEquals(listOf("a", "c", "d"), r.apps.map { it.packageName })
        assertEquals(listOf("b"), r.removed)
    }

    @Test fun rejectedDiffResendsTheWholeList() {
        val r = InventoryDiff.full(listOf(app("a")))
        assertTrue(r.full)
        assertNull(r.baseRev)
        assertEquals(listOf("a"), r.apps.map { it.packageName })
        assertEquals(emptyList<String>(), r.removed)
    }
}
