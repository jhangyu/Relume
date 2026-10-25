package io.github.jhangyu.relume.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ServerUrlTest {
    private fun rejectedPart(text: String): String =
        assertThrows(InvalidServerUrl::class.java) { ServerUrl.parse(text) }.part

    @Test
    fun cleartextRangeEdges() {
        val accepted = listOf(
            "localhost", "127.0.0.1", "127.255.255.255", "10.0.0.0", "10.255.255.255",
            "172.16.0.0", "172.31.255.255", "192.168.0.0", "192.168.255.255",
            "100.64.0.0", "100.127.255.255", "box.tail1234.ts.net", "[::1]", "[fc00::1]", "[fdff:ffff::1]",
        )
        for (host in accepted) {
            assertEquals(host, "http://$host:8080", ServerUrl.parse("http://$host:8080").toString())
        }
        val rejected = listOf(
            "9.255.255.255", "11.0.0.0", "172.15.255.255", "172.32.0.0", "192.167.255.255", "192.169.0.0",
            "100.63.255.255", "100.128.0.0", "8.8.8.8", "example.com", "ts.net.example.com", "[fe00::1]",
            "[2001:db8::1]", "10.0.0.1.nip.io",
        )
        for (host in rejected) {
            assertEquals(host, "host", rejectedPart("http://$host"))
            assertEquals(host, "https://$host", ServerUrl.parse("https://$host").toString())
        }
    }

    @Test
    fun rejectsPathQueryUserinfo() {
        assertEquals("path", rejectedPart("https://relume.example.com/relume"))
        assertEquals("query", rejectedPart("https://relume.example.com?x=1"))
        assertEquals("userinfo", rejectedPart("https://user:pw@relume.example.com"))
        assertEquals("fragment", rejectedPart("https://relume.example.com#top"))
        assertEquals("scheme", rejectedPart("ftp://relume.example.com"))
        assertEquals("scheme", rejectedPart("relume.example.com"))
        assertEquals("https://relume.example.com", ServerUrl.parse(" HTTPS://Relume.Example.com/ ").toString())
        assertEquals(
            "https://relume.example.com/api/jobs?status=running",
            ServerUrl.parse("https://relume.example.com").resolve("/api/jobs?status=running").toString(),
        )
    }

    @Test
    fun rejectsIpv6ZoneName() {
        assertThrows(InvalidServerUrl::class.java) { ServerUrl.parse("http://[fe80::1%wlan0]:8080") }
    }
}
