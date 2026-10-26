package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.ScheduleSpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Interval chips and the custom form produce only specs the server's `_check_spec` accepts. */
class ScheduleSpecTest {
    @Test
    fun presetChipMatchesHoursSpecOnly() {
        assertEquals(2, presetIndex(ScheduleSpec("hours", hours = 6)))
        assertEquals(4, presetIndex(ScheduleSpec("hours", hours = 24)))
        assertNull(presetIndex(ScheduleSpec("hours", hours = 5)))
        assertNull(presetIndex(ScheduleSpec("day", at = "09:00")))
    }

    @Test
    fun customFormBuildsValidSpecsOrNothing() {
        assertEquals("""{"every":"hours","hours":6}""", specJson("hours", "6", "", emptySet()).toString())
        assertNull(specJson("hours", "0", "", emptySet()))
        assertNull(specJson("hours", "169", "", emptySet()))
        assertEquals("""{"every":"day","at":"09:00"}""", specJson("day", "", "09:00", emptySet()).toString())
        assertNull(specJson("day", "", "9:00", emptySet()))
        assertNull(specJson("week", "", "09:00", emptySet()))
        assertEquals("""{"every":"week","days":[0,6],"at":"03:00"}""", specJson("week", "", "03:00", setOf(6, 0)).toString())
    }
}
