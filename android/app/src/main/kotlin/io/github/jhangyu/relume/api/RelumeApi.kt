package io.github.jhangyu.relume.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.serializer
import java.net.HttpURLConnection
import java.net.URLEncoder

/** HTTP failure or unreadable response: [status] (the HTTP status), server `error` text, request path. */
class ApiError(val status: Int, override val message: String, val path: String) : Exception() {
    override fun toString() = "HTTP $status $path: $message"
}

/** All HTTP calls of the app (spec 5, 10). [token] is read for every request; null sends no Authorization. */
class RelumeApi(val url: ServerUrl, private val token: () -> String?) {

    suspend fun health(): Health = get(HEALTH_PATH)

    /** [health] plus the compatibility rule: throws [ApiError] (status 200, path /api/health) when the server is too old or not ok, [java.io.IOException] when unreachable. */
    suspend fun checkServer(): Health {
        val h = health()
        if (!h.ok) throw ApiError(HEALTH_OK, "server reports ok=false", HEALTH_PATH)
        if (!isCompatibleServer(h.version)) throw ApiError(HEALTH_OK, "server runs ${h.version}, this app needs ${MIN_SERVER.joinToString(".")} or newer", HEALTH_PATH)
        return h
    }

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

    suspend fun marketplace(q: String?, category: String?, origin: String?, sort: String?, page: Int?, perPage: Int = MARKET_PAGE_SIZE): MarketList {
        val query = listOfNotNull(
            q?.takeIf { it.isNotBlank() }?.let { "q=" + enc(it) },
            category?.let { "category=" + enc(it) },
            origin?.let { "origin=$it" },
            sort?.let { "sort=$it" },
            page?.let { "page=$it" },
            "per_page=$perPage",
        )
        return get("/api/marketplace" + query.joinToString("&", prefix = "?"))
    }

    /** Pull every catalog feed now; a failed feed comes back in the status, a total failure is an [ApiError] (502). */
    suspend fun refreshCatalog(): FeedStatus = send("POST", "/api/catalog/refresh", EMPTY)

    suspend fun marketApp(pkg: String): MarketDetail = get("/api/marketplace/" + enc(pkg))

    suspend fun apps(): AppList = get("/api/apps")

    suspend fun app(id: Int): AppDetail {
        val body = get<JsonObject>("/api/apps/$id")
        return AppDetail(ApiJson.decodeFromJsonElement<AppObject>(body), ApiJson.decodeFromJsonElement<AppSettingsEnvelope>(body).settings)
    }

    /** Enable [pkg] with the chosen bundle. [patches] holds only the switches the user changed. [download] sets the optional URLs. */
    suspend fun enable(pkg: String, bundleId: Int, patches: Map<String, Boolean>?, download: JsonObject?): AppObject =
        send("POST", "/api/apps", buildJsonObject {
            put("package", pkg)
            put("bundle_id", bundleId)
            patches?.let { p -> put("patches", buildJsonObject { p.forEach { (k, v) -> put(k, v) } }) }
            download?.let { put("download", it) }
        })

    suspend fun putApp(id: Int, body: JsonObject): AppObject = send("PUT", "/api/apps/$id", body)

    suspend fun disable(id: Int) = sendNoContent("DELETE", "/api/apps/$id")

    suspend fun patches(id: Int): PatchesView = get("/api/apps/$id/patches")

    suspend fun putPatches(id: Int, body: JsonObject) {
        send<JsonObject>("PUT", "/api/apps/$id/patches", body)
    }

    suspend fun check(id: Int): Int = send<JobId>("POST", "/api/apps/$id/check", EMPTY).jobId

    suspend fun build(id: Int): Int = send<JobId>("POST", "/api/apps/$id/build", EMPTY).jobId

    suspend fun locate(id: Int): Int = send<JobId>("POST", "/api/apps/$id/locate", EMPTY).jobId

    suspend fun artifacts(id: Int): List<Artifact> = get<ArtifactList>("/api/apps/$id/artifacts").artifacts

    suspend fun schedule(): Schedule = get("/api/schedule")

    suspend fun putSchedule(body: JsonObject): Schedule = send("PUT", "/api/schedule", body)

    suspend fun runSchedule(): Int = send<JobId>("POST", "/api/schedule/run", EMPTY).jobId

    suspend fun otaSchedules(): List<OtaSchedule> = get<OtaScheduleList>("/api/schedules").schedules

    suspend fun createOtaSchedule(body: JsonObject): Int = send<CreatedId>("POST", "/api/schedules", body).id

    suspend fun putOtaSchedule(id: Int, body: JsonObject) {
        send<JsonObject>("PUT", "/api/schedules/$id", body)
    }

    suspend fun deleteOtaSchedule(id: Int) = sendNoContent("DELETE", "/api/schedules/$id")

    suspend fun devices(): List<DeviceView> = get<DeviceList>("/api/devices").devices

    suspend fun sources(): List<CustomSource> = get<SourceList>("/api/sources").sources

    suspend fun addSource(url: String, engine: String, assetRegex: String?, packages: List<String>?): Int =
        send<BundleCreated>("POST", "/api/sources", buildJsonObject {
            put("url", url)
            put("engine", engine)
            assetRegex?.let { put("asset_regex", it) }
            packages?.let { p -> putJsonArray("packages") { p.forEach { add(it) } } }
        }).bundleId

    suspend fun deleteSource(id: Int) = sendNoContent("DELETE", "/api/sources/$id")

    suspend fun cleanupPreview(): CleanupReport = get("/api/cleanup")

    suspend fun cleanup(): CleanupReport = send("POST", "/api/cleanup", EMPTY)

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

    /** Raw bytes of an authenticated GET (app icon). Non-2xx throws [ApiError]. */
    suspend fun bytes(path: String): ByteArray = withContext(Dispatchers.IO) {
        val conn = open(path, "GET")
        try {
            val status = conn.responseCode
            if (status !in HTTP_SUCCESS) throw errorOf(conn, status, path)
            conn.inputStream.use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
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

    private suspend fun <T> call(path: String, method: String, body: JsonObject?, de: KSerializer<T>): T {
        val (status, text) = raw(path, method, body)
        try {
            return ApiJson.decodeFromString(de, text)
        } catch (e: IllegalArgumentException) {
            // SerializationException is an IllegalArgumentException; init checks throw it directly.
            throw ApiError(status, "unreadable response: ${e.message}", path)
        }
    }

    /** DELETE-style calls whose success answer is an empty 204. */
    private suspend fun sendNoContent(method: String, path: String) {
        raw(path, method, null)
    }

    private suspend fun raw(path: String, method: String, body: JsonObject?): Pair<Int, String> =
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
                status to conn.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
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
        const val MARKET_PAGE_SIZE = 100
        private fun enc(v: String) = URLEncoder.encode(v, Charsets.UTF_8)
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

private const val HEALTH_PATH = "/api/health"
private const val HEALTH_OK = 200

/** Oldest server whose API this app speaks: 0.0.4 removed the old app routes. */
private val MIN_SERVER = listOf(0, 0, 4)

/** `RELUME_VERSION` unset: a build of the current tree (local stack, Linux gate). */
private const val DEV_VERSION = "dev"

/** [version] from `/api/health`: `dev`, or MAJOR.MINOR.PATCH (optional `v`) not below [MIN_SERVER]. */
internal fun isCompatibleServer(version: String): Boolean {
    if (version == DEV_VERSION) return true
    val parts = version.removePrefix("v").split('.').map { it.toIntOrNull() ?: return false }
    if (parts.size != MIN_SERVER.size) return false
    for (i in parts.indices) if (parts[i] != MIN_SERVER[i]) return parts[i] > MIN_SERVER[i]
    return true
}
