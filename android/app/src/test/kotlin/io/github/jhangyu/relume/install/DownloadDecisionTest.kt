package io.github.jhangyu.relume.install

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DownloadDecisionTest {
    private fun decide(status: Int, offset: Long = 0, length: Long = 100, range: String? = null, restarted: Boolean = false, location: String? = null) =
        DownloadDecision.decide(status, offset, length, range, planSize = 100, restarted = restarted, url = "/u", location = location)

    private fun assertFail(action: DownloadAction, part: String) {
        assertTrue("$action", action is DownloadAction.Fail && action.message.contains(part))
    }

    @Test
    fun rangeResponses() {
        assertEquals(DownloadAction.Append, decide(206, offset = 40, length = 60, range = "bytes 40-99/100"))
        assertFail(decide(206, offset = 40, range = "bytes 0-99/100"), "expected 'bytes 40-99/100'")
        assertFail(decide(206, offset = 40, range = "bytes 40-99/101"), "Content-Range")
        assertFail(decide(206, offset = 40, range = null), "Content-Range 'null'")

        assertEquals(DownloadAction.Truncate, decide(200, offset = 40))
        assertFail(decide(200, length = 99), "Content-Length 99, expected plan size 100")
        assertFail(decide(200, length = -1), "Content-Length -1")

        assertEquals(DownloadAction.Restart, decide(416, offset = 100))
        assertFail(decide(416, restarted = true), "416 again")

        assertFail(decide(404), "HTTP 404")
        assertFail(decide(409), "/u: HTTP 409")

        // a redirect is never followed (bearer token); the failure names where it pointed
        assertFail(decide(302, location = "https://other.example/x.apk"), "/u: HTTP 302, redirect not followed, Location: https://other.example/x.apk")
        assertFail(decide(307), "Location: (none)")
    }
}
