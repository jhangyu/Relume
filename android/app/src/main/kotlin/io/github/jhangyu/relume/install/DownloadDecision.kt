package io.github.jhangyu.relume.install

/** What to do with one download response (spec 9.2). Pure: inputs are the response facts. */
sealed interface DownloadAction {
    /** 206 continuing the `.part` file. */
    data object Append : DownloadAction
    /** 200 full body: truncate the `.part` file and write from 0. */
    data object Truncate : DownloadAction
    /** First 416: delete the `.part` file and request again from 0. */
    data object Restart : DownloadAction
    data class Fail(val message: String) : DownloadAction
}

object DownloadDecision {
    private const val HTTP_OK = 200
    private const val HTTP_PARTIAL = 206
    private const val HTTP_RANGE_NOT_SATISFIABLE = 416
    private val CONTENT_RANGE = Regex("""bytes (\d+)-(\d+)/(\d+)""")

    /**
     * [offset] = bytes already in the `.part` file (the Range start sent), [contentLength] = `Content-Length` or -1,
     * [restarted] = a 416 was already answered by a restart.
     */
    fun decide(
        status: Int,
        offset: Long,
        contentLength: Long,
        contentRange: String?,
        planSize: Long,
        restarted: Boolean,
        url: String,
    ): DownloadAction = when (status) {
        HTTP_PARTIAL -> partial(offset, contentRange, planSize, url)
        HTTP_OK ->
            if (contentLength == planSize) DownloadAction.Truncate
            else DownloadAction.Fail("$url: Content-Length $contentLength, expected plan size $planSize")
        HTTP_RANGE_NOT_SATISFIABLE ->
            if (restarted) DownloadAction.Fail("$url: HTTP 416 again after restarting from 0")
            else DownloadAction.Restart
        else -> DownloadAction.Fail("$url: HTTP $status, expected 200 or 206")
    }

    private fun partial(offset: Long, contentRange: String?, planSize: Long, url: String): DownloadAction {
        val m = contentRange?.let { CONTENT_RANGE.matchEntire(it.trim()) }
            ?: return DownloadAction.Fail("$url: HTTP 206 with Content-Range '$contentRange', expected 'bytes $offset-${planSize - 1}/$planSize'")
        val (start, end, total) = m.destructured
        return if (start.toLong() == offset && end.toLong() == planSize - 1 && total.toLong() == planSize) DownloadAction.Append
        else DownloadAction.Fail("$url: HTTP 206 with Content-Range '$contentRange', expected 'bytes $offset-${planSize - 1}/$planSize'")
    }
}
