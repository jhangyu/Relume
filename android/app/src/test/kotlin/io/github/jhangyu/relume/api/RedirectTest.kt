package io.github.jhangyu.relume.api

import com.sun.net.httpserver.HttpServer
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicInteger

/** A 3xx must not be followed (the bearer token would reach another host) and must name its Location. */
class RedirectTest {
    @Test
    fun redirectIsNotFollowedAndNamesLocation() {
        val targetHits = AtomicInteger()
        val server = HttpServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0)
        val target = "http://127.0.0.1:${server.address.port}/followed"
        server.createContext("/api/health") { ex ->
            ex.responseHeaders.add("Location", target)
            ex.sendResponseHeaders(302, -1)
            ex.close()
        }
        server.createContext("/followed") { ex -> targetHits.incrementAndGet(); ex.sendResponseHeaders(200, -1); ex.close() }
        server.start()
        try {
            val api = RelumeApi(ServerUrl.parse("http://127.0.0.1:${server.address.port}")) { "secret-token" }
            val e = assertThrows(ApiError::class.java) { runBlocking { api.health() } }
            assertEquals(302, e.status)
            assertTrue(e.message, target in e.message)
            assertEquals(0, targetHits.get())
        } finally {
            server.stop(0)
        }
    }
}
