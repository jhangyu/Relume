package io.github.jhangyu.relume.api

/** One dispatched server-sent event; [id] is the last `id:` seen on the stream (resume with `Last-Event-ID`). */
data class SseEvent(val id: String?, val event: String?, val data: String)

/** Line-fed `text/event-stream` parser (WHATWG rules for `id`, `event`, `data`, comments and blank-line dispatch). */
class SseParser(private val onEvent: (SseEvent) -> Unit) {
    private var lastId: String? = null
    private var event: String? = null
    private val data = mutableListOf<String>()

    fun line(text: String) {
        if (text.isEmpty()) return dispatch()
        if (text.startsWith(":")) return
        val colon = text.indexOf(':')
        val field = if (colon < 0) text else text.substring(0, colon)
        val value = if (colon < 0) "" else text.substring(colon + 1).removePrefix(" ")
        when (field) {
            "id" -> if ('\u0000' !in value) lastId = value
            "event" -> event = value
            "data" -> data += value
        }
    }

    private fun dispatch() {
        if (data.isNotEmpty()) onEvent(SseEvent(lastId, event, data.joinToString("\n")))
        event = null
        data.clear()
    }
}
