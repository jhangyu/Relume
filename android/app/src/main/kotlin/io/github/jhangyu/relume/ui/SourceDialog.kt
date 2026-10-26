package io.github.jhangyu.relume.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import io.github.jhangyu.relume.R

private val ENGINES = listOf("morphe", "lspatch", "npatch")

/** Dialog behind the top-bar "Add custom source" button (URL, engine, module options). */
@Composable
fun AddSourceDialog(model: MarketModel) {
    if (!model.addSourceOpen) return
    var url by remember { mutableStateOf("") }
    var engine by remember { mutableStateOf(ENGINES.first()) }
    var regex by remember { mutableStateOf("") }
    var packages by remember { mutableStateOf("") }
    val module = engine != ENGINES.first()
    RDialog(model::closeAddSource) {
        Txt("Add custom source", style = MaterialTheme.typography.headlineSmall)
        Field("GitHub repository URL", url, { url = it }, leadingIcon = R.drawable.ms_link)
        ChipRow(ENGINES, ENGINES.indexOf(engine)) { engine = ENGINES[it] }
        model.error?.let { Banner("Could not add source", Tone.Danger, detail = it) }
        if (module) {
            Field("Asset regex", regex, { regex = it }, mono = true)
            Field("Target packages (comma separated)", packages, { packages = it }, mono = true)
        }
        DialogButtons {
            ActionButton("Cancel", model::closeAddSource, Modifier.weight(1f), kind = ButtonKind.Text)
            ActionButton(
                "Add",
                { model.addSource(url.trim(), engine, regex.trim().takeIf { module && it.isNotEmpty() }, packages.split(',').map { it.trim() }.filter { it.isNotEmpty() }.takeIf { module && it.isNotEmpty() }) },
                Modifier.weight(1f),
                enabled = url.isNotBlank(),
            )
        }
    }
}
