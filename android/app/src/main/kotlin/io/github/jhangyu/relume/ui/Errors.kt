package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.ApiError
import java.io.IOException

/** Spec 10: errors show HTTP status + server `error` text + path; transport failures show the exception text. */
fun describe(e: Exception): String = when (e) {
    is ApiError -> e.toString()
    is IOException -> "${e.javaClass.simpleName}: ${e.message ?: "no detail"}"
    else -> throw e
}
