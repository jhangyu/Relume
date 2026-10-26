package io.github.jhangyu.relume.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.Schedule
import io.github.jhangyu.relume.api.ScheduleSpec

private val WEEKDAYS = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

internal fun describeSpec(spec: ScheduleSpec): String = when (spec.every) {
    "hours" -> "Every ${spec.hours} hours"
    "day" -> "Every day at ${spec.at}"
    "week" -> "Every ${spec.days.orEmpty().joinToString(", ") { WEEKDAYS[it] }} at ${spec.at}"
    else -> throw IllegalArgumentException("schedule spec.every: expected hours, day or week, got '${spec.every}'")
}

/** Schedules tab (thin first version): the global update interval as text and Run now. */
@Composable
fun SchedulesScreen(model: AppModel) {
    val schedule: Schedule? = model.schedule
    Column(Modifier.fillMaxSize()) {
        AppBar("Schedules", actions = { Avatar() })
        model.error?.let { Banner("Request failed", Tone.Danger, Modifier.padding(horizontal = ScreenPadding), detail = it, action = "Dismiss", onAction = model::dismissError) }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SectionHeader("Update schedule")
            if (schedule == null) {
                Txt("Loading…", color = muted())
            } else {
                InsetGroup {
                    ListRow(
                        describeSpec(schedule.spec),
                        supporting = "Next run: ${formatTime(schedule.nextRunAt)} · last run: ${formatTime(schedule.lastRunAt)}",
                        leading = { Glyph(R.drawable.ms_schedule) },
                    )
                }
                ActionButton("Run now", model::runSchedule, Modifier.fillMaxWidth(), icon = R.drawable.ms_play_arrow)
            }
        }
    }
}
