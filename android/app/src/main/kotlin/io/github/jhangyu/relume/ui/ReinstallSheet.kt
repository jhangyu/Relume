package io.github.jhangyu.relume.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Bottom sheet frame of the install lightbox and the reinstall confirm (mockup install-lightbox-b). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun RSheet(onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
    ) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            content = content,
        )
    }
}

/** Mockup frame 4: data-loss confirm before the system uninstall of an app signed by another key (spec install-states 3). */
@Composable
fun ReinstallSheet(name: String, detail: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    RSheet(onDismiss) {
        Txt("Reinstall $name?", style = MaterialTheme.typography.headlineSmall)
        Txt(detail, style = MaterialTheme.typography.bodySmall, color = muted())
        Banner(
            "Data and login are erased",
            Tone.Danger,
            detail = "Android cannot update the copy on your phone in place, so Relume must remove it first. You will sign in again.",
        )
        Txt("Android shows its uninstall prompt next. After that, the new build installs automatically.", style = MaterialTheme.typography.bodyMedium, color = muted())
        DialogButtons {
            ActionButton("Cancel", onDismiss, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton("Erase and reinstall", onConfirm, Modifier.weight(1f), kind = ButtonKind.Danger)
        }
    }
}
