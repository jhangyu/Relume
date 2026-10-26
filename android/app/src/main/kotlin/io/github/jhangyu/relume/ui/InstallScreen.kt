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
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.Composable
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

/** Frames 5a and 5b: one block per plan step, phases for the running ones, buttons for the ones that wait for the user. */
@Composable
fun InstallScreen(run: RunState?, model: AppModel, onClose: () -> Unit) {
    val ctx = LocalContext.current
    var uninstallIndex by remember { mutableStateOf<Int?>(null) }
    val corrupt by InstallRunner.corrupt.collectAsState()
    Column(Modifier.fillMaxSize()) {
        AppBar("Install", onBack = onClose)
        corrupt?.let { e ->
            Column(Modifier.padding(ScreenPadding), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Banner("The saved install run is corrupt", Tone.Danger, detail = e.message)
                ActionButton("Reset install run", { InstallRunner.reset(ctx) }, Modifier.fillMaxWidth(), kind = ButtonKind.Danger)
            }
            return@Column
        }
        if (run == null) {
            Txt("No install run.", Modifier.padding(ScreenPadding), color = muted())
            return@Column
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = ScreenPadding, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            if (run.stalePlan) {
                Banner("The plan changed on the server", Tone.Warn, detail = "Nothing was reinstalled. Go back, refresh and start again.", action = "Refresh", onAction = model::refresh)
            }
            run.syncError?.let { Banner("Could not report to the server", Tone.Danger, detail = it) }
            run.outboxError?.let { Banner("Could not send an earlier result to the server", Tone.Danger, detail = it) }
            run.steps.forEachIndexed { i, s -> StepBlock(s, i, ctx, model) { uninstallIndex = i } }
        }
        Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer).padding(horizontal = ScreenPadding, vertical = 10.dp)) {
            if (run.isActive || run.steps.any { it.state == StepState.PENDING }) {
                ActionButton("Cancel", { InstallRunner.cancel(ctx) }, Modifier.fillMaxWidth(), kind = ButtonKind.Outlined)
            } else {
                ActionButton("Done", onClose, Modifier.fillMaxWidth(), kind = ButtonKind.Primary)
            }
        }
    }
    val index = uninstallIndex
    val waiting = if (index != null) run?.steps?.getOrNull(index) else null
    if (index != null && waiting != null && waiting.state == StepState.NEEDS_UNINSTALL) {
        UninstallDialog(waiting, onDismiss = { uninstallIndex = null }) {
            uninstallIndex = null
            InstallRunner.uninstallAndRetry(ctx, index)
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

/** Frame 5c: uninstall first. The text names both signers or both version codes (the step's error text). */
@Composable
private fun UninstallDialog(s: StepRun, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    var understood by remember { mutableStateOf(false) }
    RDialog(onDismiss) {
        DialogIcon(R.drawable.ms_warning, Tone.Danger)
        Txt(conflictTitle(s.step), style = MaterialTheme.typography.headlineSmall)
        s.errorText?.let { Txt(it, color = muted()) }
        Banner("App data will be deleted", Tone.Danger, detail = "Uninstalling deletes this app's data on the phone (logins, downloads, settings). This cannot be undone.")
        CheckRow("I understand, uninstall first", understood) { understood = it }
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Uninstall and install", onConfirm, Modifier.weight(1f), kind = ButtonKind.Danger, enabled = understood)
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
