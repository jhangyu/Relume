package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.Download
import io.github.jhangyu.relume.api.RelumeApi
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.CancellationException

/** A step failed for a reason that ends it (not an IO error, which the worker retries). */
class StepFailure(val code: String, message: String) : Exception(message)

/** Integrity headers of the download response (spec 6.5, 7.2); null = header absent. */
data class VerifyHeaders(val sha256: String?, val signerSha256: String?)

class DownloadedApk(val file: File, val headers: VerifyHeaders)

/** Streams a plan download into `<dir>/<sha256>.part`, resuming with Range + If-Range (spec 9.2). Blocking. */
object Downloader {
    const val HEADER_SHA256 = "X-Relume-Sha256"
    const val HEADER_SIGNER = "X-Relume-Signer-Sha256"
    private const val BUFFER = 64 * 1024

    fun partFile(dir: File, d: Download) = File(dir, "${d.sha256}.part")

    /**
     * [stopped] is checked between buffers; true throws [CancellationException] and keeps the `.part` file.
     * [progress] gets the `.part` length after every buffer.
     */
    fun download(api: RelumeApi, d: Download, dir: File, stopped: () -> Boolean, progress: (Long) -> Unit): DownloadedApk {
        check(dir.isDirectory || dir.mkdirs()) { "download directory $dir: cannot create" }
        val part = partFile(dir, d)
        var restarted = false
        while (true) {
            val offset = if (part.exists()) part.length() else 0L
            // The server's ETag is the quoted sha256 (spec 6.5), so the plan gives If-Range without a stored ETag.
            val conn = api.openDownload(d.url, offset, if (offset > 0) "\"${d.sha256}\"" else null)
            try {
                val action = DownloadDecision.decide(
                    conn.responseCode, offset, conn.contentLengthLong, conn.getHeaderField("Content-Range"),
                    d.size, restarted, d.url,
                )
                when (action) {
                    is DownloadAction.Fail -> throw StepFailure(ErrorCodes.INSTALLER_ERROR, action.message)
                    DownloadAction.Restart -> {
                        check(!part.exists() || part.delete()) { "${part.path}: cannot delete after HTTP 416" }
                        restarted = true
                        continue
                    }
                    DownloadAction.Append, DownloadAction.Truncate -> {
                        val append = action == DownloadAction.Append
                        var written = if (append) offset else 0L
                        FileOutputStream(part, append).use { out ->
                            conn.inputStream.use { input ->
                                val buf = ByteArray(BUFFER)
                                while (true) {
                                    if (stopped()) throw CancellationException("download of ${d.url} stopped by WorkManager")
                                    val n = input.read(buf)
                                    if (n < 0) break
                                    out.write(buf, 0, n)
                                    written += n
                                    progress(written)
                                }
                            }
                            out.fd.sync()
                        }
                    }
                }
                if (part.length() != d.size) {
                    throw StepFailure(ErrorCodes.INSTALLER_ERROR, "${d.url}: body ended at ${part.length()} bytes, expected plan size ${d.size}")
                }
                return DownloadedApk(part, VerifyHeaders(conn.getHeaderField(HEADER_SHA256), conn.getHeaderField(HEADER_SIGNER)))
            } finally {
                conn.disconnect()
            }
        }
    }
}
