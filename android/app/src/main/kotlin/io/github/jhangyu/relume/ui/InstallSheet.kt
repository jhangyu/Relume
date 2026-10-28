package io.github.jhangyu.relume.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.material3.MaterialTheme
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.InstallRunner
import io.github.jhangyu.relume.install.RunState
import io.github.jhangyu.relume.install.StepRun
import io.github.jhangyu.relume.install.StepState
import io.github.jhangyu.relume.inventory.Inventory

private const val PHASE_DOWNLOAD = 0
private const val PHASE_VERIFY = 1
private const val PHASE_SYSTEM = 2
private val PHASES = listOf("Download", "Verify sha256 and signer", "System install")
private const val KEY_PREVIEW = 16

private enum class PhaseMark { Done, Active, Waiting }

/** Which of the three phases is done or active for a step state. */
private fun phaseMarks(state: StepState): List<PhaseMark> {
    val current = when (state) {
        StepState.PENDING, StepState.DOWNLOADING -> PHASE_DOWNLOAD
        StepState.VERIFYING -> PHASE_VERIFY
        else -> PHASE_SYSTEM
    }
    val allDone = state == StepState.INSTALLED
    return PHASES.indices.map { i ->
        when {
            allDone || i < current -> PhaseMark.Done
            i == current && state != StepState.PENDING -> PhaseMark.Active
            else -> PhaseMark.Waiting
        }
    }
}

private fun StepState.label(): String = when (this) {
    StepState.PENDING -> "Waiting"
    StepState.DOWNLOADING -> "Downloading"
    StepState.VERIFYING -> "Verifying"
    StepState.COMMITTED -> "Installing"
    StepState.AWAITING_USER -> "Needs your confirmation"
    StepState.NEEDS_UNINSTALL -> "Needs uninstall"
    StepState.INSTALLED -> "Installed"
    StepState.UP_TO_DATE -> "Already up to date"
    StepState.FAILED -> "Failed"
    StepState.CANCELLED -> "Cancelled"
    StepState.DEPENDENCY_FAILED -> "Dependency failed"
    StepState.SERVER_ERROR -> "Server refused"
}

@Composable
private fun StepState.pastel(): Pair<Color, Color> {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    return when (this) {
        StepState.INSTALLED, StepState.UP_TO_DATE -> r.green.container to r.green.onContainer
        StepState.FAILED, StepState.NEEDS_UNINSTALL, StepState.DEPENDENCY_FAILED, StepState.SERVER_ERROR -> s.errorContainer to s.onErrorContainer
        StepState.AWAITING_USER -> r.amber.container to r.amber.onContainer
        StepState.CANCELLED, StepState.PENDING -> s.surfaceContainerHigh to s.onSurfaceVariant
        StepState.DOWNLOADING, StepState.VERIFYING, StepState.COMMITTED -> r.blue.container to r.blue.onContainer
    }
}

/** Lightbox state (spec install-states 2): open while running, closes itself on success, stays open on failure. */
internal enum class SheetOutcome { Running, Done, Failed }

private val CLOSING = setOf(StepState.INSTALLED, StepState.UP_TO_DATE, StepState.CANCELLED)

/** [reinstallPending]: package whose `needs_uninstall` step the user confirmed; the system uninstall comes next. */
internal fun sheetOutcome(run: RunState, reinstallPending: String?): SheetOutcome = when {
    run.steps.any { it.state.active || it.state == StepState.PENDING } -> SheetOutcome.Running
    run.steps.any { it.state == StepState.NEEDS_UNINSTALL && it.step.packageName == reinstallPending } -> SheetOutcome.Running
    run.steps.all { it.state in CLOSING } -> SheetOutcome.Done
    else -> SheetOutcome.Failed
}

private fun sheetTitle(run: RunState, outcome: SheetOutcome): String {
    if (outcome == SheetOutcome.Failed) return "Install failed"
    val installs = run.steps.filter { it.step.action == PlanAction.INSTALL }
    return if (installs.size == 1) "Installing ${installs[0].step.displayName}" else "Installing ${installs.size} apps"
}

/** Mockup install-lightbox-b frames 2 and 3: the install run as a bottom sheet over the current tab. */
@Composable
fun InstallSheet(run: RunState?, model: AppModel) {
    val ctx = LocalContext.current
    val corrupt by InstallRunner.corrupt.collectAsState()
    var confirmIndex by remember { mutableStateOf<Int?>(null) }
    // "<revision>/<step>" whose system uninstall was already opened: one prompt per step, not one per recomposition.
    var asked by remember { mutableStateOf<String?>(null) }
    val pending = model.reinstallPackage
    val outcome = run?.let { sheetOutcome(it, pending) }

    // Spec install-states 3: a confirmed needs_uninstall step goes to the system uninstall prompt; success re-runs it.
    LaunchedEffect(run, pending) {
        val r = run ?: return@LaunchedEffect
        val i = r.steps.indexOfFirst { it.state == StepState.NEEDS_UNINSTALL && it.step.packageName == pending }
        val key = "${r.revision}/$i"
        if (i >= 0 && asked != key) {
            asked = key
            InstallRunner.uninstallAndRetry(ctx, i)
        }
    }
    LaunchedEffect(outcome, corrupt) {
        if (outcome == SheetOutcome.Done && corrupt == null) model.closeSheet()
    }

    RSheet(model::closeSheet) {
        corrupt?.let { e ->
            Banner("The saved install run is corrupt", Tone.Danger, detail = e.message)
            ActionButton("Reset install run", { InstallRunner.reset(ctx) }, Modifier.fillMaxWidth(), kind = ButtonKind.Danger)
            return@RSheet
        }
        if (run == null || outcome == null) {
            Txt("No install run.", color = muted())
            return@RSheet
        }
        Txt(sheetTitle(run, outcome), style = MaterialTheme.typography.headlineSmall)
        if (run.stalePlan) Banner("The plan changed on the server", Tone.Warn, detail = "Nothing was reinstalled. Retry fetches the new plan.")
        run.syncError?.let { Banner("Could not report to the server", Tone.Danger, detail = it) }
        run.outboxError?.let { Banner("Could not send an earlier result to the server", Tone.Danger, detail = it) }
        run.steps.forEachIndexed { i, s -> StepBlock(s, i, ctx, model) { confirmIndex = i } }
        DialogButtons {
            if (outcome == SheetOutcome.Failed) {
                ActionButton("Close", model::closeSheet, Modifier.weight(1f), kind = ButtonKind.Text)
                ActionButton("Retry", model::retryInstall, Modifier.weight(1f), kind = ButtonKind.Install)
            } else {
                ActionButton("Cancel", { InstallRunner.cancel(ctx) }, Modifier.weight(1f), kind = ButtonKind.Text)
            }
        }
        if (outcome != SheetOutcome.Failed) Txt("Closes by itself when done", style = MaterialTheme.typography.bodySmall, color = muted())
    }

    val index = confirmIndex
    val waiting = if (index != null) run?.steps?.getOrNull(index) else null
    if (index != null && waiting != null && waiting.state == StepState.NEEDS_UNINSTALL) {
        val detail = listOfNotNull(waiting.step.download?.versionName, conflictTitle(waiting.step)).joinToString(" · ")
        ReinstallSheet(waiting.step.displayName, detail, onDismiss = { confirmIndex = null }) {
            confirmIndex = null
            model.markReinstall(waiting.step.packageName)   // the LaunchedEffect above opens the system uninstall
        }
    }
}

@Composable
private fun StepBlock(s: StepRun, index: Int, ctx: Context, model: AppModel, onUninstall: () -> Unit) {
    val step = s.step
    val icon = if (step.kind == StepKind.ITEM) model.app(step.refId)?.iconUrl else null // project steps have no icon
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
            AppTile(step.displayName, icon, model.api, 56.dp)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Txt(step.displayName, style = MaterialTheme.typography.headlineSmall)
                step.download?.let { d -> Txt("${d.versionName ?: d.versionCode} · ${formatMb(d.size)}", style = Mono, color = muted()) }
            }
            val (container, onContainer) = s.state.pastel()
            Pill(s.state.label(), container, onContainer)
        }
        when (s.state) {
            StepState.PENDING, StepState.DOWNLOADING, StepState.VERIFYING, StepState.COMMITTED, StepState.INSTALLED, StepState.AWAITING_USER -> {
                PhaseList(s.state)
                val d = step.download
                if (s.state == StepState.DOWNLOADING && d != null) {
                    LinearProgressIndicator(
                        { s.downloadedBytes.toFloat() / d.size },
                        Modifier.fillMaxWidth(),
                        color = MaterialTheme.relume.blue.onContainer,
                        trackColor = MaterialTheme.relume.blue.container,
                    )
                    Txt("${formatMb(s.downloadedBytes)} of ${formatMb(d.size)}", style = Mono, color = muted())
                }
                if (s.state == StepState.PENDING && Inventory.needsConfirmation(ctx, step.packageName)) {
                    Txt("One confirmation needed in the Android dialog.", style = MaterialTheme.typography.bodyMedium, color = muted())
                }
                if (s.state == StepState.AWAITING_USER) {
                    ActionButton("Confirm", { InstallRunner.confirm(ctx, index) }, Modifier.fillMaxWidth(), kind = ButtonKind.Install)
                }
            }
            StepState.NEEDS_UNINSTALL -> {
                Banner(conflictTitle(step), Tone.Danger, detail = s.errorText)
                ActionButton("Uninstall and install…", onUninstall, Modifier.fillMaxWidth(), kind = ButtonKind.Danger)
            }
            StepState.UP_TO_DATE -> Unit
            StepState.FAILED, StepState.CANCELLED, StepState.DEPENDENCY_FAILED, StepState.SERVER_ERROR ->
                Banner(s.state.label(), Tone.Danger, detail = listOfNotNull(s.errorCode, s.errorText).joinToString(": ").ifEmpty { step.reason })
        }
        s.receiptError?.let { Banner("Could not send the result to the server", Tone.Danger, detail = it) }
    }
}

private fun conflictTitle(step: PlanStep) = if (step.signerConflict) "Signature doesn't match" else "A newer version is installed"

@Composable
private fun PhaseList(state: StepState) {
    val ok = MaterialTheme.relume.green.onContainer
    val active = MaterialTheme.colorScheme.secondary
    val idle = MaterialTheme.colorScheme.outline
    Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
        phaseMarks(state).forEachIndexed { i, mark ->
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(24.dp)
                        .clip(CircleShape)
                        .background(if (mark == PhaseMark.Done) ok else Color.Transparent)
                        .border(1.5.dp, if (mark == PhaseMark.Active) active else if (mark == PhaseMark.Done) ok else idle, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    when (mark) {
                        PhaseMark.Done -> Glyph(R.drawable.ms_check, Color.White, 14.dp)
                        PhaseMark.Active -> Box(Modifier.size(10.dp).clip(CircleShape).background(active))
                        PhaseMark.Waiting -> Unit
                    }
                }
                Txt(PHASES[i], color = if (mark == PhaseMark.Waiting) muted() else MaterialTheme.colorScheme.onSurface, bold = mark != PhaseMark.Waiting)
            }
        }
    }
}

/** Frame 5d, the signer-change dialog and the install-permission explanation, driven by [AppModel.prompt]. */
@Composable
fun InstallPrompts(model: AppModel) {
    val ctx = LocalContext.current
    when (val p = model.prompt) {
        null -> Unit
        is AppModel.InstallPrompt.NeedPermission -> RDialog(model::dismissPrompt) {
            DialogIcon(R.drawable.ms_warning, Tone.Warn)
            Txt("Allow installs from Relume", style = MaterialTheme.typography.headlineSmall)
            Txt("Android blocks app installs until you allow Relume under “Install unknown apps”. Come back here afterwards and start the install again.", color = muted())
            DialogButtons {
                ActionButton("Cancel", model::dismissPrompt, Modifier.weight(1f), kind = ButtonKind.Text)
                ActionButton("Open settings", {
                    ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}")))
                    model.dismissPrompt()
                }, Modifier.weight(1f), kind = ButtonKind.Primary)
            }
        }
        is AppModel.InstallPrompt.Dependencies -> RDialog(model::dismissPrompt) {
            val installs = p.plan.steps.filter { it.action == PlanAction.INSTALL }
            DialogIcon(R.drawable.ms_install_mobile, Tone.Ok)
            Txt("Install ${installs.first().displayName} first", style = MaterialTheme.typography.headlineSmall)
            Txt("${installs.last().displayName} needs ${installs.dropLast(1).joinToString { it.displayName }}. They are installed in this order:", color = muted())
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                installs.forEachIndexed { i, s -> DependencyRow(i + 1, s) }
            }
            DialogButtons {
                ActionButton("Cancel", model::dismissPrompt, Modifier.weight(1f), kind = ButtonKind.Text)
                ActionButton("Install all", { model.confirmDependencies(p) }, Modifier.weight(1f), kind = ButtonKind.Install)
            }
        }
        is AppModel.InstallPrompt.Signer -> RDialog(model::dismissPrompt) {
            val change = checkNotNull(p.step.signerChange) { "signer prompt for ${p.step.displayName} without signer_change" }
            DialogIcon(R.drawable.ms_warning, Tone.Danger)
            Txt("Signing key changed", style = MaterialTheme.typography.headlineSmall)
            Txt("The signing key of ${p.step.displayName} on the server changed.", color = muted())
            Txt("pinned   ${change.pinned?.take(KEY_PREVIEW) ?: "none"}\noffered  ${change.offered.take(KEY_PREVIEW)}", style = Mono)
            Banner("Accepting trusts a new signing key for ${p.step.displayName}", Tone.Danger)
            DialogButtons {
                ActionButton("Cancel", model::dismissPrompt, Modifier.weight(1f), kind = ButtonKind.Text)
                ActionButton("Accept key", { model.acceptSigner(p) }, Modifier.weight(1f), kind = ButtonKind.Danger)
            }
        }
    }
}

@Composable
private fun DependencyRow(number: Int, s: PlanStep) {
    val active = MaterialTheme.colorScheme.secondary
    Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(24.dp).clip(CircleShape).border(1.5.dp, if (number == 1) active else MaterialTheme.colorScheme.outline, CircleShape), contentAlignment = Alignment.Center) {
            if (number == 1) Box(Modifier.size(10.dp).clip(CircleShape).background(active))
        }
        Column {
            Txt("$number · ${s.displayName}", bold = true)
            s.download?.let { Txt("${it.versionName ?: it.versionCode} · ${formatMb(it.size)}", style = Mono, color = muted()) }
        }
    }
}
