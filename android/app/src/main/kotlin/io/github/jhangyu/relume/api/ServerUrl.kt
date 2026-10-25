package io.github.jhangyu.relume.api

import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI
import java.net.URISyntaxException
import java.net.URL

class InvalidServerUrl(val part: String, message: String) : Exception("$part: $message")

/** Server base address: scheme, host, optional port. http only for private hosts (spec 8.5). */
class ServerUrl private constructor(val scheme: String, val host: String, val port: Int) {

    override fun toString(): String {
        val h = if (':' in host) "[$host]" else host
        return if (port == NO_PORT) "$scheme://$h" else "$scheme://$h:$port"
    }

    override fun equals(other: Any?) = other is ServerUrl && other.toString() == toString()

    override fun hashCode() = toString().hashCode()

    /** [path] is absolute and may carry a query, e.g. `/api/jobs?status=running`. */
    fun resolve(path: String): URL {
        require(path.startsWith("/")) { "path must start with '/', got '$path'" }
        return URI("$this$path").toURL()
    }

    companion object {
        private const val NO_PORT = -1
        private const val HTTP = "http"
        private const val HTTPS = "https"

        fun parse(text: String): ServerUrl {
            val trimmed = text.trim()
            val uri = try {
                URI(trimmed)
            } catch (e: URISyntaxException) {
                throw InvalidServerUrl("address", "'$trimmed' is not a URL: ${e.reason}")
            }
            val scheme = uri.scheme?.lowercase()
                ?: throw InvalidServerUrl("scheme", "missing, expected http:// or https://")
            if (scheme != HTTP && scheme != HTTPS) {
                throw InvalidServerUrl("scheme", "expected http or https, got '$scheme'")
            }
            if (uri.rawUserInfo != null) throw InvalidServerUrl("userinfo", "remove '${uri.rawUserInfo}@'")
            val path = uri.rawPath.orEmpty()
            if (path.isNotEmpty() && path != "/") throw InvalidServerUrl("path", "remove '$path'")
            if (uri.rawQuery != null) throw InvalidServerUrl("query", "remove '?${uri.rawQuery}'")
            if (uri.rawFragment != null) throw InvalidServerUrl("fragment", "remove '#${uri.rawFragment}'")
            val rawHost = uri.host ?: throw InvalidServerUrl("host", "missing or invalid in '$trimmed'")
            val host = rawHost.removePrefix("[").removeSuffix("]").lowercase()
            if (scheme == HTTP && !isPrivateHost(host)) {
                throw InvalidServerUrl("host", "use https:// for public addresses ('$host' is not a private address)")
            }
            return ServerUrl(scheme, host, uri.port)
        }

        /** Checked by name or literal, never by resolving DNS. */
        internal fun isPrivateHost(host: String): Boolean {
            if (host == "localhost" || host.endsWith(".ts.net")) return true
            if (':' in host) return isPrivateIpv6(host)
            val octets = ipv4Octets(host) ?: return false
            val (a, b) = octets
            return a == 127 || a == 10 ||
                (a == 172 && b in 16..31) ||
                (a == 192 && b == 168) ||
                (a == 100 && b in 64..127)
        }

        private fun ipv4Octets(host: String): List<Int>? {
            val parts = host.split('.')
            if (parts.size != 4) return null
            val octets = parts.map { p -> p.takeIf { it.isNotEmpty() && it.all(Char::isDigit) }?.toIntOrNull() }
            return octets.filterNotNull().takeIf { o -> o.size == 4 && o.all { it in 0..255 } }
        }

        private fun isPrivateIpv6(host: String): Boolean {
            // A literal containing ':' is parsed by getByName without a DNS lookup.
            val addr = InetAddress.getByName(host) as? Inet6Address ?: return false
            val first = addr.address[0].toInt() and 0xFF
            return addr.isLoopbackAddress || (first and 0xFE) == 0xFC
        }
    }
}
