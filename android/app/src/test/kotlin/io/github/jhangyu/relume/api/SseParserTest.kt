package io.github.jhangyu.relume.api

import org.junit.Assert.assertEquals
import org.junit.Test

class SseParserTest {
    @Test
    fun parsesIdDataEndAndComments() {
        val events = mutableListOf<SseEvent>()
        val parser = SseParser { events += it }
        // Server format (jobs.log_stream): id + multi-line data, keepalive comments, then event: end.
        listOf(
            ": keepalive", "",
            "id: 12", "data: [+] step one", "data: second line", "",
            "data:no space", "",
            ": keepalive", "",
            "event: end", "data: success", "",
        ).forEach(parser::line)
        assertEquals(
            listOf(
                SseEvent("12", null, "[+] step one\nsecond line"),
                SseEvent("12", null, "no space"),
                SseEvent("12", "end", "success"),
            ),
            events,
        )
    }
}
