package io.github.jhangyu.relume.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.Item
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep

private const val BUILD_FAILED = "failed"

enum class RowKind(val label: String) {
    Building("Building…"),
    Failed("Build failed"),
    Update("Update available"),
    NotInstalled("Not installed"),
    Blocked("Install blocked"),
    UpToDate("Up to date"),
}

/** What the phone knows about one catalog item: its installed package, the server plan step (null if not assigned). */
class AppFacts(val item: Item, val installed: InstalledPackage?, val step: PlanStep?) {
    /** Version the install would put on the phone. */
    val target: String? get() = step?.download?.versionName ?: item.lastSuccess?.appVersion

    val kind: RowKind
        get() = when {
            item.activeJob != null -> RowKind.Building
            step?.action == PlanAction.INSTALL -> if (installed == null) RowKind.NotInstalled else RowKind.Update
            step?.action == PlanAction.ERROR -> RowKind.Blocked
            item.lastBuild?.status == BUILD_FAILED -> RowKind.Failed
            step?.action == PlanAction.UP_TO_DATE -> RowKind.UpToDate
            installed == null -> RowKind.NotInstalled
            item.lastSuccess?.appVersion != null && item.lastSuccess.appVersion != installed.versionName -> RowKind.Update
            else -> RowKind.UpToDate
        }

    /** An install can start: the plan offers one, or a build exists and the item is not yet planned. */
    val installable: Boolean
        get() = when (kind) {
            RowKind.Update, RowKind.NotInstalled -> step?.action == PlanAction.INSTALL || (step == null && item.lastSuccess != null)
            else -> false
        }
}

@Composable
fun RowKind.color(): Color = with(FluentTheme.colors) {
    when (this@color) {
        RowKind.Building -> info
        RowKind.Failed, RowKind.Blocked -> danger
        RowKind.Update -> warn
        RowKind.NotInstalled -> fg3
        RowKind.UpToDate -> ok
    }
}
