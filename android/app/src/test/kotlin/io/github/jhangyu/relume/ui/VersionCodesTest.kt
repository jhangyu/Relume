package io.github.jhangyu.relume.ui

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Test
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull

class VersionCodesTest {
    @Test fun parsesCommaAndSpaceSeparatedInts() {
        assertEquals(listOf(476015655, 476015536), parseVersionCodes("476015655, 476015536"))
        assertEquals(listOf(1, 2), parseVersionCodes(" 1 2 "))
        assertEquals(emptyList<Int>(), parseVersionCodes("  "))
        assertNull(parseVersionCodes("12a"))
        assertNull(parseVersionCodes("99999999999"))
    }

    @Test fun writesListOrNullIntoEveryVariantDownload() {
        val v = JsonObject(mapOf("abi" to JsonPrimitive("universal"), "download" to JsonObject(mapOf("kind" to JsonPrimitive("apkmirror")))))
        val set = withVersionCodes(listOf(v, v), listOf(7))
        assertEquals(listOf(7), set.map { versionCodesOf(it) }.distinct().single())
        assertEquals("apkmirror", kindOf(set[0]))
        val cleared = withVersionCodes(set, emptyList())
        assertEquals(JsonNull, (cleared[0]["download"] as JsonObject)["version_codes"])
        assertEquals(emptyList<Int>(), versionCodesOf(cleared[0]))
    }
}
