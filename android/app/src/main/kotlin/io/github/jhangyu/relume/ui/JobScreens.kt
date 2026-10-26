package io.github.jhangyu.relume.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.api.BuildProgress
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.Job
import io.github.jhangyu.relume.api.RelumeApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val LOG_BG = Color(0xFF0B0D0F)
private val LOG_FG = Color(0xFFC9D1D9)
private val LOG_STEP = Color(0xFF7AA2F7)
private val LOG_OK = Color(0xFF3FB97F)
private val LOG_WARN = Color(0xFFE5B04A)
private const val STATUS_QUEUED = "queued"
private const val STATUS_RUNNING = "running"
private const val STATUS_SUCCESS = "success"
private const val STATUS_FAILED = "failed"
private const val STATUS_CANCELLED = "cancelled"

private val DATE_TIME = DateTimeFormatter.ofPattern("MMM d · HH:mm", Locale.ENGLISH)

internal fun formatTime(iso: String?): String =
    if (iso == null) "–" else DATE_TIME.format(Instant.parse(iso).atZone(ZoneId.systemDefault()))

internal fun isActive(status: String) = status == STATUS_QUEUED || status == STATUS_RUNNING

@Composable
fun JobChip(status: String) {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    when (status) {
        STATUS_QUEUED -> Pill("Queued", s.surfaceContainerHigh, s.onSurfaceVariant)
        STATUS_RUNNING -> Pill("Building", r.blue.container, r.blue.onContainer)
        STATUS_SUCCESS -> Pill("Built", r.green.container, r.green.onContainer)
        STATUS_FAILED -> Pill("Failed", s.errorContainer, s.onErrorContainer)
        STATUS_CANCELLED -> Pill("Cancelled", s.surfaceContainerHigh, s.onSurfaceVariant)
        else -> Pill(status, s.surfaceContainerHigh, s.onSurfaceVariant)
    }
}

/** Frame 4: one SSE stream per opened job; resumes from the last event id on the retry button, never by itself. */
@Composable
fun LogScreen(api: RelumeApi, jobId: Int, progress: BuildProgress?, label: String?, onBack: () -> Unit, onEnded: () -> Unit) {
    val lines = remember(jobId) { mutableStateListOf<String>() }
    var job by remember(jobId) { mutableStateOf<Job?>(null) }
    var error by remember(jobId) { mutableStateOf<String?>(null) }
    var cancelError by remember(jobId) { mutableStateOf<String?>(null) }
    var lastId by remember(jobId) { mutableStateOf<String?>(null) }
    var attempt by remember(jobId) { mutableIntStateOf(0) }
    var follow by remember(jobId) { mutableStateOf(true) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()

    LaunchedEffect(jobId, attempt) {
        error = null
        try {
            job = api.job(jobId)
            withContext(Dispatchers.IO) {
                api.jobLog(jobId, lastId) { ev ->
                    ev.id?.let { lastId = it }
                    if (ev.event != RelumeApi.END_EVENT) lines.addAll(ev.data.split('\n'))
                }
            }
            job = api.job(jobId)
            onEnded()
        } catch (e: ApiError) {
            error = describe(e)
        } catch (e: IOException) {
            error = describe(e)
        }
    }
    LaunchedEffect(lines.size, follow) {
        if (follow && lines.isNotEmpty()) listState.scrollToItem(lines.size - 1)
    }

    Column(Modifier.fillMaxSize()) {
        AppBar("Job #$jobId", label, onBack = onBack)
        Column(Modifier.weight(1f).padding(horizontal = ScreenPadding, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                job?.let { JobChip(it.status) }
                if (job != null && progress != null) Txt("${progress.step} ${progress.index}/${progress.count}", style = MonoSmall, color = muted())
            }
            if (progress != null && progress.count > 0) {
                LinearProgressIndicator(
                    { progress.index.toFloat() / progress.count },
                    Modifier.fillMaxWidth(),
                    color = MaterialTheme.relume.blue.onContainer,
                    trackColor = MaterialTheme.relume.blue.container,
                )
            }
            cancelError?.let { Banner("Cancel failed", Tone.Danger, detail = it) }
            error?.let { Banner("Log stream stopped", Tone.Danger, detail = it, action = "Resume", onAction = { attempt++ }) }
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(LOG_BG).padding(12.dp),
            ) {
                items(lines.size) { i -> LogLine(lines[i]) }
            }
        }
        Row(
            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer).padding(horizontal = ScreenPadding, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(Modifier.weight(1f)) { SwitchRow("Follow", null, follow) { follow = it } }
            job?.takeIf { isActive(it.status) }?.let {
                ActionButton("Cancel build", kind = ButtonKind.Outlined, onClick = {
                    scope.launch {
                        try {
                            api.cancelJob(jobId)
                            job = api.job(jobId)
                        } catch (e: ApiError) {
                            cancelError = describe(e)
                        } catch (e: IOException) {
                            cancelError = describe(e)
                        }
                    }
                })
            }
        }
    }
}

@Composable
private fun LogLine(text: String) {
    val color = when {
        text.startsWith("▶") -> LOG_STEP
        text.startsWith("✓") -> LOG_OK
        text.startsWith("!") -> LOG_WARN
        else -> LOG_FG
    }
    Txt(text, style = MonoSmall, color = color)
}
