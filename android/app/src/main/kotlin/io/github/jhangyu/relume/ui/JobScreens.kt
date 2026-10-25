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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.api.ActiveJob
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.Job
import io.github.jhangyu.relume.api.RelumeApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
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
private const val BUILDS_PAGE_SIZE = 30

private val DATE_TIME = DateTimeFormatter.ofPattern("MMM d · HH:mm", Locale.ENGLISH)

internal fun formatTime(iso: String?): String =
    if (iso == null) "–" else DATE_TIME.format(Instant.parse(iso).atZone(ZoneId.systemDefault()))

internal fun formatDuration(start: String?, end: String?): String {
    if (start == null || end == null) return ""
    val s = Duration.between(Instant.parse(start), Instant.parse(end)).seconds
    return if (s >= 60) "${s / 60}m ${s % 60}s" else "${s}s"
}

internal fun isActive(status: String) = status == STATUS_QUEUED || status == STATUS_RUNNING

@Composable
fun JobChip(status: String) {
    val c = FluentTheme.colors
    when (status) {
        STATUS_QUEUED -> Chip("Queued", c.fg3)
        STATUS_RUNNING -> Chip("Building…", c.info)
        STATUS_SUCCESS -> Chip("Built", c.ok)
        STATUS_FAILED -> Chip("Failed", c.danger)
        STATUS_CANCELLED -> Chip("Cancelled", c.fg3)
        else -> Chip(status, c.fg3)
    }
}

internal fun Job.itemId(): Int? = (target["item_id"] as? JsonPrimitive)?.intOrNull

/** Frame 4: one SSE stream per opened job; resumes from the last event id on the retry button, never by itself. */
@Composable
fun LogScreen(api: RelumeApi, jobId: Int, active: ActiveJob?, label: String?, onBack: () -> Unit, onEnded: () -> Unit) {
    val c = FluentTheme.colors
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
        AppBar("Build #$jobId", label, onBack = onBack)
        Column(Modifier.weight(1f).padding(horizontal = ScreenPadding, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                job?.let { JobChip(it.status) }
                val j = job
                if (j != null) {
                    val progress = if (active != null && active.stepIndex != null && active.stepCount != null) "${active.step ?: ""} ${active.stepIndex}/${active.stepCount}" else ""
                    Txt(progress.trim(), style = FluentType.monoSmall, color = c.fg3)
                }
            }
            if (active?.stepIndex != null && active.stepCount != null && active.stepCount > 0) {
                Box(Modifier.fillMaxWidth().heightIn(max = 3.dp).clip(FluentShapes.control).background(c.stroke)) {
                    Box(Modifier.fillMaxWidth(active.stepIndex.toFloat() / active.stepCount).heightIn(min = 3.dp).background(c.accent))
                }
            }
            cancelError?.let { MessageBar("Cancel failed", Tone.Danger, detail = it) }
            error?.let { MessageBar("Log stream stopped", Tone.Danger, detail = it, action = "Resume", onAction = { attempt++ }) }
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth().clip(FluentShapes.card).background(LOG_BG).border(1.dp, c.stroke, FluentShapes.card).padding(12.dp),
            ) {
                items(lines.size) { i -> LogLine(lines[i]) }
            }
        }
        Hairline()
        Row(Modifier.fillMaxWidth().background(c.bg2).padding(horizontal = ScreenPadding, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            ToggleLabel("Follow", follow, true) { follow = it }
            Spacer(Modifier.weight(1f))
            job?.takeIf { isActive(it.status) }?.let {
                FButton("Cancel build", {
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
    Txt(text, style = FluentType.monoSmall, color = color)
}

/** Builds tab: `GET /api/jobs?kind=build`, paged with `before_id`. */
@Composable
fun BuildsScreen(api: RelumeApi, labelOf: (Int?) -> String?, onOpen: (Int) -> Unit, reloadKey: Int) {
    val c = FluentTheme.colors
    val jobs = remember { mutableStateListOf<Job>() }
    var error by remember { mutableStateOf<String?>(null) }
    var more by remember { mutableStateOf(true) }
    var loading by remember { mutableStateOf(false) }
    var request by remember { mutableIntStateOf(0) }

    LaunchedEffect(reloadKey, request) {
        loading = true
        error = null
        try {
            val before = if (request == 0) null else jobs.lastOrNull()?.id
            val page = api.jobs("build", null, before, BUILDS_PAGE_SIZE)
            if (before == null) jobs.clear()
            jobs.addAll(page)
            more = page.size == BUILDS_PAGE_SIZE
        } catch (e: ApiError) {
            error = describe(e)
        } catch (e: IOException) {
            error = describe(e)
        } finally {
            loading = false
        }
    }

    Column(Modifier.fillMaxSize()) {
        AppBar("Builds")
        error?.let { MessageBar("Could not load builds", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it) }
        LazyColumn(Modifier.weight(1f).padding(horizontal = ScreenPadding, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(jobs.size) { i ->
                val j = jobs[i]
                FCard(Modifier.clickable { onOpen(j.id) }) {
                    Row(Modifier.heightIn(min = 52.dp).padding(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Txt("#${j.id}", style = FluentType.mono)
                        Column(Modifier.weight(1f)) {
                            Txt(labelOf(j.itemId()) ?: "Item ${j.itemId() ?: "?"}", bold = true, maxLines = 1)
                            Txt(formatTime(j.createdAt), style = FluentType.small, color = c.fg3)
                        }
                        JobChip(j.status)
                        Txt(formatDuration(j.startedAt, j.finishedAt), style = FluentType.monoSmall, color = c.fg3)
                    }
                }
            }
            if (more && jobs.isNotEmpty()) {
                item { FButton("Load older", { request++ }, Modifier.fillMaxWidth(), enabled = !loading) }
            }
            if (jobs.isEmpty() && !loading && error == null) {
                item { Txt("No builds yet.", color = c.fg3) }
            }
        }
    }
}
