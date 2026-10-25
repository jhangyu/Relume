package io.github.jhangyu.relume.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.serializer
import java.net.HttpURLConnection

/** HTTP failure or unreadable response: [status] (the HTTP status), server `error` text, request path. */
class ApiError(val status: Int, override val message: String, val path: String) : Exception() {
    override fun toString() = "HTTP $status $path: $message"
}

/** All HTTP calls of the app (spec 5, 10). [token] is read for every request; null sends no Authorization. */
class RelumeApi(val url: ServerUrl, private val token: () -> String?) {

    suspend fun health(): Health = get("/api/health")

    suspend fun registerDevice(user: String, password: String, device: DeviceInfo): Registration =
        send("POST", "/api/auth/device", buildJsonObject {
            put("username", user)
            put("password", password)
            put("device", ApiJson.encodeToJsonElement(DeviceInfo.serializer(), device))
        })

    suspend fun adopt(deviceId: Int): Device =
        send<DeviceEnvelope>("POST", "/api/me/adopt", buildJsonObject { put("device_id", deviceId) }).device

    suspend fun me(): Me = get("/api/me")

    suspend fun sync(report: SyncReport): SyncResponse =
        send("POST", "/api/me/sync", ApiJson.encodeToJsonElement(SyncReport.serializer(), report) as JsonObject)

    suspend fun plan(itemIds: List<Int>?, projectIds: List<Int>?): InstallPlan {
        val query = listOfNotNull(
            itemIds?.let { "items=" + it.joinToString(",") },
            projectIds?.let { "projects=" + it.joinToString(",") },
        )
        val path = "/api/me/plan" + if (query.isEmpty()) "" else query.joinToString("&", prefix = "?")
        return get<PlanEnvelope>(path).plan
    }

    suspend fun receipt(r: InstallReceipt) {
        send<JsonObject>("POST", "/api/me/installs", ApiJson.encodeToJsonElement(InstallReceipt.serializer(), r) as JsonObject)
    }

    suspend fun logout() {
        send<JsonObject>("POST", "/api/me/logout", EMPTY)
    }

    suspend fun catalog(): Catalog = get("/api/catalog")

    suspend fun item(id: Int): Item = get("/api/items/$id")

    suspend fun artifacts(id: Int): List<Artifact> = get<ArtifactList>("/api/items/$id/artifacts").artifacts

    suspend fun setSwitches(id: Int, enabled: Boolean?, autoUpdate: Boolean?): Item =
        send("PUT", "/api/items/$id", buildJsonObject {
            enabled?.let { put("enabled", it) }
            autoUpdate?.let { put("auto_update", it) }
        })

    suspend fun build(id: Int): Int = send<JobId>("POST", "/api/items/$id/build", EMPTY).jobId

    suspend fun update(id: Int, rebuild: Boolean): List<Int> =
        send<JobIds>("POST", "/api/items/$id/update", buildJsonObject { put("rebuild", rebuild) }).jobIds

    suspend fun checkAll() {
        send<JsonObject>("POST", "/api/upstreams/check", EMPTY)
    }

    suspend fun jobs(kind: String?, status: String?, beforeId: Int?, limit: Int?): List<Job> {
        val query = listOfNotNull(
            kind?.let { "kind=$it" },
            status?.let { "status=$it" },
            beforeId?.let { "before_id=$it" },
            limit?.let { "limit=$it" },
        )
        return get<JobList>("/api/jobs" + if (query.isEmpty()) "" else query.joinToString("&", prefix = "?")).jobs
    }

    suspend fun job(id: Int): Job = get("/api/jobs/$id")

    suspend fun cancelJob(id: Int) {
        send<JsonObject>("POST", "/api/jobs/$id/cancel", EMPTY)
    }

    suspend fun projects(): List<Project> = get<ProjectList>("/api/projects").projects

    suspend fun acceptSigner(projectId: Int, signerSha256: String): Project =
        send("POST", "/api/projects/$projectId/signer/accept", buildJsonObject { put("signer_sha256", signerSha256) })

    suspend fun putAssignments(deviceId: Int, itemIds: List<Int>, projectIds: List<Int>?): Device =
        send<DeviceEnvelope>("PUT", "/api/devices/$deviceId/assignments", buildJsonObject {
            putJsonArray("item_ids") { itemIds.forEach { add(it) } }
            projectIds?.let { ids -> putJsonArray("project_ids") { ids.forEach { add(it) } } }
        }).device

    /**
     * Blocking; call from a worker thread. Opens [path] (e.g. a plan `download.url`) and returns the connected
     * connection; the caller reads the status (200, 206, 416, ...) and the body.
     */
    fun openDownload(path: String, from: Long, ifRange: String?): HttpURLConnection {
        val conn = open(path, "GET")
        if (from > 0) conn.setRequestProperty("Range", "bytes=$from-")
        ifRange?.let { conn.setRequestProperty("If-Range", it) }
        conn.connect()
        return conn
    }

    /** Blocking SSE read of a job log; returns after the `end` event. Call from a worker thread. */
    fun jobLog(id: Int, lastEventId: String?, onEvent: (SseEvent) -> Unit) {
        val path = "/api/jobs/$id/log"
        val conn = open(path, "GET")
        conn.setRequestProperty("Accept", "text/event-stream")
        lastEventId?.let { conn.setRequestProperty("Last-Event-ID", it) }
        try {
            val status = conn.responseCode
            if (status != HttpURLConnection.HTTP_OK) throw errorOf(conn, status, path)
            var ended = false
            val parser = SseParser { event ->
                onEvent(event)
                if (event.event == END_EVENT) ended = true
            }
            conn.inputStream.bufferedReader(Charsets.UTF_8).use { reader ->
                while (!ended) {
                    val line = reader.readLine()
                        ?: throw ApiError(status, "log stream closed before the end event", path)
                    parser.line(line)
                }
            }
        } finally {
            conn.disconnect()
        }
    }

    private suspend inline fun <reified T> get(path: String): T = call(path, "GET", null, serializer())

    private suspend inline fun <reified T> send(method: String, path: String, body: JsonObject): T =
        call(path, method, body, serializer())

    private suspend fun <T> call(path: String, method: String, body: JsonObject?, de: KSerializer<T>): T =
        withContext(Dispatchers.IO) {
            val conn = open(path, method)
            try {
                if (body != null) {
                    conn.doOutput = true
                    conn.setRequestProperty("Content-Type", JSON_TYPE)
                    conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
                }
                val status = conn.responseCode
                if (status !in HTTP_SUCCESS) throw errorOf(conn, status, path)
                val text = conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
                try {
                    ApiJson.decodeFromString(de, text)
                } catch (e: IllegalArgumentException) {
                    // SerializationException is an IllegalArgumentException; init checks throw it directly.
                    throw ApiError(status, "unreadable response: ${e.message}", path)
                }
            } finally {
                conn.disconnect()
            }
        }

    private fun open(path: String, method: String): HttpURLConnection {
        val conn = url.resolve(path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.instanceFollowRedirects = false // a redirect could carry the bearer token to another host
        conn.connectTimeout = CONNECT_TIMEOUT_MS
        conn.readTimeout = READ_TIMEOUT_MS
        conn.setRequestProperty("Accept", JSON_TYPE)
        token()?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
        return conn
    }

    private fun errorOf(conn: HttpURLConnection, status: Int, path: String): ApiError {
        if (status in HTTP_REDIRECTS) {
            return ApiError(status, redirectMessage(conn.getHeaderField("Location")), path)
        }
        val text = conn.errorStream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
        return ApiError(status, errorMessage(text), path)
    }

    internal companion object {
        /** Server `{"error": msg, ...}` → "msg (key: value, ...)" so `attempts_left`/`retry_after` reach the UI. */
        internal fun redirectMessage(location: String?): String =
            "redirect not followed, Location: ${location ?: "(none)"}"

        internal fun errorMessage(body: String): String {
            val obj = try {
                ApiJson.parseToJsonElement(body) as? JsonObject
            } catch (e: SerializationException) {
                null
            }
            val error = (obj?.get("error") as? JsonPrimitive)?.takeIf { it.isString }?.content
                ?: return "non-JSON error body: " + body.take(ERROR_BODY_PREVIEW).ifEmpty { "(empty)" }
            val extras = obj.filterKeys { it != "error" }.entries.joinToString(", ") { (k, v) -> "$k: $v" }
            return if (extras.isEmpty()) error else "$error ($extras)"
        }


        private const val JSON_TYPE = "application/json"
        const val END_EVENT = "end"
        const val CONNECT_TIMEOUT_MS = 15_000
        /** Longer than the server's 15 s SSE keepalive. */
        const val READ_TIMEOUT_MS = 60_000
        const val ERROR_BODY_PREVIEW = 200
        val HTTP_SUCCESS = 200..299
        val HTTP_REDIRECTS = 300..399
        val EMPTY = JsonObject(emptyMap())
    }
}
