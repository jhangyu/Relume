package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.ApiError
import java.io.IOException

/** Spec 10: errors show HTTP status + server `error` text + path; transport failures show the exception text. */
fun describe(e: Exception): String = when (e) {
    is ApiError -> e.toString()
    is IOException -> "${e.javaClass.simpleName}: ${e.message ?: "no detail"}"
    else -> throw e
}

/** [block]'s value, or null after [onFailure] got its [ApiError] or [IOException]. Every other exception propagates. */
inline fun <T> catchApi(onFailure: (Exception) -> Unit, block: () -> T): T? = try {
    block()
} catch (e: Exception) {
    if (e !is ApiError && e !is IOException) throw e
    onFailure(e)
    null
}
