package io.github.jhangyu.relume.ui

import android.graphics.BitmapFactory
import android.util.Log
import android.util.LruCache
import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import io.github.jhangyu.relume.R
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.RelumeApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.Locale

/** Smallest touch target. */
val TouchTarget = 48.dp
internal val ScreenPadding = 16.dp
private val ICON_SIZE = 24.dp
private val INSET_GROUP_RADIUS = 16.dp
private val PILL_ICON_SIZE = 14.dp
private val DIALOG_RADIUS = 28.dp
private val MONO = TextStyle(fontSize = 12.sp, fontFamily = FontFamily.Monospace)
private const val DISABLED_ALPHA = 0.38f
private const val BYTES_PER_MB = 1024.0 * 1024.0

internal val Mono: TextStyle get() = MONO

internal fun formatMb(bytes: Long): String = String.format(Locale.ENGLISH, "%.1f MB", bytes / BYTES_PER_MB)

@Composable
fun Txt(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = MaterialTheme.typography.bodyLarge,
    color: Color = MaterialTheme.colorScheme.onSurface,
    maxLines: Int = Int.MAX_VALUE,
    bold: Boolean = false,
) {
    Text(
        text = text,
        modifier = modifier,
        style = style,
        color = color,
        fontWeight = if (bold) FontWeight.Medium else null,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )
}

/** Secondary text colour. */
@Composable
internal fun muted(): Color = MaterialTheme.colorScheme.onSurfaceVariant

@Composable
fun Glyph(@DrawableRes icon: Int, tint: Color = MaterialTheme.colorScheme.onSurfaceVariant, size: Dp = ICON_SIZE) {
    Icon(painterResource(icon), contentDescription = null, tint = tint, modifier = Modifier.size(size))
}

/** What an icon button means: the glyph colour follows tokens.md section 3. */
enum class IconTone { Plain, Build, Sync, Link }

/** 48 dp icon button. Build is the tonal rebuild button (primaryContainer). */
@Composable
fun IconAction(@DrawableRes icon: Int, contentDescription: String, onClick: () -> Unit, tone: IconTone = IconTone.Plain, enabled: Boolean = true) {
    val glyph = when (tone) {
        IconTone.Plain -> MaterialTheme.colorScheme.onSurfaceVariant
        IconTone.Build -> MaterialTheme.relume.buildGlyph
        IconTone.Sync -> MaterialTheme.relume.syncGlyph
        IconTone.Link -> MaterialTheme.relume.linkGlyph
    }
    val painted = @Composable {
        Icon(painterResource(icon), contentDescription = contentDescription, tint = glyph, modifier = Modifier.size(ICON_SIZE))
    }
    if (tone == IconTone.Build) {
        FilledTonalIconButton(
            onClick,
            enabled = enabled,
            colors = IconButtonDefaults.filledTonalIconButtonColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
            content = painted,
        )
    } else {
        IconButton(onClick, enabled = enabled, content = painted)
    }
}

/** Pastel pill: label (and optional glyph) on a container colour. */
@Composable
fun Pill(text: String, container: Color, onContainer: Color, @DrawableRes icon: Int? = null) {
    Row(
        Modifier.clip(RoundedCornerShape(50)).background(container).padding(horizontal = 10.dp, vertical = 3.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) Glyph(icon, onContainer, PILL_ICON_SIZE)
        Txt(text, style = MaterialTheme.typography.labelMedium, color = onContainer, maxLines = 1)
    }
}

/** Build status of an enabled app (`status.state`). */
@Composable
fun StatusPill(state: String) {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    when (state) {
        "up_to_date" -> Pill("Up to date", r.green.container, r.green.onContainer, R.drawable.ms_check)
        "update_available" -> Pill("New version", r.lavender.container, r.lavender.onContainer, R.drawable.ms_sync)
        "building" -> Pill("Building", r.blue.container, r.blue.onContainer, R.drawable.ms_progress_activity)
        "queued" -> Pill("Queued", r.blue.container, r.blue.onContainer)
        "needs_download_url" -> Pill("Needs URL", r.amber.container, r.amber.onContainer, R.drawable.ms_link)
        "failed" -> Pill("Failed", s.errorContainer, s.onErrorContainer, R.drawable.ms_error)
        "not_built" -> Pill("Not built", s.surfaceContainerHigh, s.onSurfaceVariant)
        else -> throw IllegalArgumentException("status.state: expected one of up_to_date, update_available, building, queued, failed, not_built, needs_download_url, got '$state'")
    }
}

/** Official catalog = blue, Built-in = teal, Custom = pink. */
@Composable
fun OriginLabel(origin: String) {
    val r = MaterialTheme.relume
    when (origin) {
        "official" -> Pill("Official catalog", r.blue.container, r.blue.onContainer)
        "builtin" -> Pill("Built-in", r.teal.container, r.teal.onContainer)
        "custom" -> Pill("Custom", r.pink.container, r.pink.onContainer)
        else -> throw IllegalArgumentException("origin: expected official, builtin or custom, got '$origin'")
    }
}

/** Category colours of tokens.md section 3: first = container, second = tile gradient end. */
@Composable
private fun categoryPastels(category: String?): Pair<Pastel, Pastel> {
    val r = MaterialTheme.relume
    return when (category?.lowercase(Locale.ENGLISH)) {
        "social" -> r.pink to r.lavender
        "video", "video players & editors" -> r.coral to r.amber
        "music", "music & audio" -> r.amber to r.coral
        "tools" -> r.teal to r.blue
        "productivity" -> r.blue to r.teal
        "personalization" -> r.lavender to r.pink
        "maps", "maps & navigation" -> r.green to r.teal
        "news", "news & magazines" -> r.blue to r.lavender
        else -> r.blue to r.lavender
    }
}

@Composable
fun CategoryTag(category: String?) {
    if (category == null) return
    val (first, _) = categoryPastels(category)
    Pill(category, first.container, first.onContainer)
}

/** MD3 inset list group: white container, 16 dp radius. */
@Composable
fun InsetGroup(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(modifier.fillMaxWidth(), shape = RoundedCornerShape(INSET_GROUP_RADIUS), color = MaterialTheme.colorScheme.surfaceContainerLowest) {
        Column(content = content)
    }
}

@Composable
fun SectionHeader(text: String) {
    Box(Modifier.fillMaxWidth().height(TouchTarget).padding(horizontal = 4.dp), contentAlignment = Alignment.CenterStart) {
        Txt(text, style = MaterialTheme.typography.titleSmall, color = muted())
    }
}

enum class ButtonKind { Primary, Tonal, Outlined, Text, Install, Danger }

/** Button of the Relume palette: violet = build, sky = choose, coral = send to the phone (Install, Update). */
@Composable
fun ActionButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: ButtonKind = ButtonKind.Primary,
    enabled: Boolean = true,
    @DrawableRes icon: Int? = null,
) {
    val s = MaterialTheme.colorScheme
    val label: @Composable RowScope.() -> Unit = {
        if (icon != null) {
            Glyph(icon, contentOf(kind), 18.dp)
            Spacer(Modifier.width(8.dp))
        }
        Text(text, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.labelLarge)
    }
    when (kind) {
        ButtonKind.Primary -> Button(onClick, modifier, enabled, content = label)
        ButtonKind.Install -> Button(onClick, modifier, enabled, colors = ButtonDefaults.buttonColors(containerColor = s.tertiary, contentColor = s.onTertiary), content = label)
        ButtonKind.Danger -> Button(onClick, modifier, enabled, colors = ButtonDefaults.buttonColors(containerColor = s.error, contentColor = s.onError), content = label)
        ButtonKind.Tonal -> Button(onClick, modifier, enabled, colors = ButtonDefaults.buttonColors(containerColor = s.secondaryContainer, contentColor = s.onSecondaryContainer), content = label)
        ButtonKind.Outlined -> OutlinedButton(
            onClick, modifier, enabled,
            colors = ButtonDefaults.outlinedButtonColors(contentColor = s.secondary),
            border = BorderStroke(1.dp, if (enabled) s.secondary else s.secondary.copy(alpha = DISABLED_ALPHA)),
            content = label,
        )
        ButtonKind.Text -> TextButton(onClick, modifier, enabled, colors = ButtonDefaults.textButtonColors(contentColor = s.secondary), content = label)
    }
}

@Composable
private fun contentOf(kind: ButtonKind): Color {
    val s = MaterialTheme.colorScheme
    return when (kind) {
        ButtonKind.Primary -> s.onPrimary
        ButtonKind.Install -> s.onTertiary
        ButtonKind.Danger -> s.onError
        ButtonKind.Tonal -> s.onSecondaryContainer
        ButtonKind.Outlined, ButtonKind.Text -> s.secondary
    }
}

enum class Tone { Info, Ok, Danger, Warn }

/** Message strip: pastel container, bold headline, optional detail and action link. */
@Composable
fun Banner(
    headline: String,
    tone: Tone,
    modifier: Modifier = Modifier,
    detail: String? = null,
    action: String? = null,
    onAction: () -> Unit = {},
) {
    val (container, onContainer) = tone.colors()
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(container).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Txt(headline, style = MaterialTheme.typography.titleMedium, color = onContainer)
        if (detail != null) Txt(detail, style = MaterialTheme.typography.bodyMedium, color = onContainer)
        if (action != null) {
            Txt(
                action,
                Modifier.heightIn(min = TouchTarget).clickable(role = Role.Button, onClick = onAction).padding(top = 12.dp),
                style = MaterialTheme.typography.labelLarge,
                color = onContainer,
                bold = true,
            )
        }
    }
}

@Composable
internal fun Tone.colors(): Pair<Color, Color> {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    return when (this) {
        Tone.Info -> r.blue.container to r.blue.onContainer
        Tone.Ok -> r.green.container to r.green.onContainer
        Tone.Warn -> r.amber.container to r.amber.onContainer
        Tone.Danger -> s.errorContainer to s.onErrorContainer
    }
}

/** Top app bar: 64 dp, optional back arrow, title with optional mono subtitle, trailing actions. */
@Composable
fun AppBar(title: String, subtitle: String? = null, onBack: (() -> Unit)? = null, actions: @Composable RowScope.() -> Unit = {}) {
    Row(
        Modifier.fillMaxWidth().height(64.dp).padding(start = if (onBack != null) 4.dp else 16.dp, end = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (onBack != null) IconAction(R.drawable.ms_arrow_back, "Back", onBack)
        Column(Modifier.weight(1f)) {
            Txt(title, style = MaterialTheme.typography.titleLarge, maxLines = 1)
            if (subtitle != null) Txt(subtitle, style = MonoSmall, color = muted(), maxLines = 1)
        }
        actions()
    }
}

/** The signed-in account as the top app bars show it: avatar initials and what a tap does (open Settings). */
class Account(val initials: String, val openSettings: () -> Unit)

val LocalAccount = staticCompositionLocalOf<Account> { error("RelumeRoot provides LocalAccount above every screen") }

/** Account avatar of the top app bar; opens Settings. */
@Composable
fun Avatar() {
    val account = LocalAccount.current
    Box(Modifier.size(TouchTarget), contentAlignment = Alignment.Center) {
        Box(
            Modifier.size(32.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primaryContainer)
                .semantics { contentDescription = "Phone settings and account" }
                .clickable(role = Role.Button, onClick = account.openSettings),
            contentAlignment = Alignment.Center,
        ) {
            Txt(account.initials, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
        }
    }
}

/** Horizontal filter chips; the selected one is secondaryContainer with a check. */
@Composable
fun ChipRow(labels: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    val s = MaterialTheme.colorScheme
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = ScreenPadding, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        labels.forEachIndexed { i, label ->
            FilterChip(
                selected = i == selected,
                onClick = { onSelect(i) },
                label = { Text(label, maxLines = 1) },
                leadingIcon = if (i == selected) ({ Glyph(R.drawable.ms_check, s.onSecondaryContainer, 18.dp) }) else null,
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = s.secondaryContainer,
                    selectedLabelColor = s.onSecondaryContainer,
                ),
                border = FilterChipDefaults.filterChipBorder(
                    enabled = true,
                    selected = i == selected,
                    borderColor = s.outline,
                    selectedBorderColor = Color.Transparent,
                ),
            )
        }
    }
}

/** Two-line list row: leading slot, headline, supporting text, trailing slot; min 56/72 dp. */
@Composable
fun ListRow(
    headline: String,
    modifier: Modifier = Modifier,
    supporting: String? = null,
    leading: @Composable (() -> Unit)? = null,
    trailing: @Composable (() -> Unit)? = null,
) {
    Row(
        modifier.fillMaxWidth().heightIn(min = if (supporting == null) 56.dp else 72.dp).padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        leading?.invoke()
        Column(Modifier.weight(1f)) {
            Txt(headline, maxLines = 1)
            if (supporting != null) Txt(supporting, style = MaterialTheme.typography.bodyMedium, color = muted(), maxLines = 2)
        }
        trailing?.invoke()
    }
}

/** Settings-style row: title and help text on the left, switch on the right; the whole row toggles. */
@Composable
fun SwitchRow(title: String, help: String?, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange)
            .padding(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Txt(title)
            if (help != null) Txt(help, style = MaterialTheme.typography.bodyMedium, color = muted())
        }
        Switch(checked = checked, onCheckedChange = null, enabled = enabled)
    }
}

/** Radio row: the whole row is the 48 dp target. */
@Composable
fun RadioRow(selected: Boolean, enabled: Boolean = true, onSelect: () -> Unit, content: @Composable RowScope.() -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = TouchTarget)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onSelect)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null, enabled = enabled, colors = RadioButtonDefaults.colors(selectedColor = MaterialTheme.colorScheme.secondary))
        content()
    }
}

@Composable
fun CheckRow(text: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = TouchTarget).toggleable(value = checked, role = Role.Checkbox, onValueChange = onChange),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = checked, onCheckedChange = null, colors = CheckboxDefaults.colors(checkedColor = MaterialTheme.colorScheme.secondary))
        Txt(text)
    }
}

/** Outlined text field (56 dp); focus colour = secondary. A secret field gets a Show/Hide text button. */
@Composable
fun Field(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    @DrawableRes leadingIcon: Int? = null,
    mono: Boolean = false,
    secret: Boolean = false,
    error: String? = null,
    hint: String? = null,
    enabled: Boolean = true,
    keyboard: KeyboardOptions = KeyboardOptions.Default,
) {
    val s = MaterialTheme.colorScheme
    var reveal by remember { mutableStateOf(false) }
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        modifier = modifier.fillMaxWidth(),
        enabled = enabled,
        singleLine = true,
        label = { Text(label) },
        leadingIcon = leadingIcon?.let { icon -> { Glyph(icon) } },
        trailingIcon = if (secret) ({ TextButton({ reveal = !reveal }) { Text(if (reveal) "Hide" else "Show", color = s.secondary) } }) else null,
        textStyle = if (mono) MONO else MaterialTheme.typography.bodyLarge,
        isError = error != null,
        supportingText = (error ?: hint)?.let { text -> { Text(text) } },
        visualTransformation = if (secret && !reveal) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = keyboard,
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = s.secondary,
            focusedLabelColor = s.secondary,
            cursorColor = s.secondary,
            unfocusedBorderColor = s.outline,
        ),
    )
}

/** Dialog shell: 28 dp corners, 24 dp padding. */
@Composable
fun RDialog(onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(DIALOG_RADIUS), color = MaterialTheme.colorScheme.surfaceContainerHigh) {
            Column(Modifier.fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.spacedBy(14.dp), content = content)
        }
    }
}

@Composable
fun DialogIcon(@DrawableRes icon: Int, tone: Tone) {
    val (container, onContainer) = tone.colors()
    Box(Modifier.size(44.dp).clip(CircleShape).background(container), contentAlignment = Alignment.Center) { Glyph(icon, onContainer) }
}

@Composable
fun DialogButtons(content: @Composable RowScope.() -> Unit) {
    Row(Modifier.fillMaxWidth().padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), content = content)
}

@Composable
fun Hairline(modifier: Modifier = Modifier) {
    HorizontalDivider(modifier, color = MaterialTheme.colorScheme.outlineVariant)
}

/**
 * App tile. Shows the server icon at [iconUrl] when there is one, the initials tile (two-container gradient picked
 * from the label) while it loads, when [iconUrl] is null and when the fetch fails (logged with package path and status).
 */
@Composable
fun AppTile(label: String, iconUrl: String?, api: RelumeApi?, size: Dp = 40.dp) {
    val r = MaterialTheme.relume
    val pairs = listOf(r.pink to r.lavender, r.coral to r.amber, r.amber to r.coral, r.teal to r.blue, r.blue to r.teal, r.lavender to r.pink, r.green to r.teal)
    val (first, second) = pairs[label.hashCode().mod(pairs.size)]
    val icon by produceState<ImageBitmap?>(null, iconUrl, api) { value = if (iconUrl != null && api != null) loadIcon(api, iconUrl) else null }
    Box(
        Modifier.size(size).clip(RoundedCornerShape(size / 4)).background(Brush.linearGradient(listOf(first.container, second.container))),
        contentAlignment = Alignment.Center,
    ) {
        val bmp = icon
        if (bmp != null) {
            Image(bmp, contentDescription = label, modifier = Modifier.size(size))
        } else {
            Txt(initials(label), style = TextStyle(fontSize = if (size > 48.dp) 18.sp else 13.sp, fontWeight = FontWeight.Medium), color = first.onContainer)
        }
    }
}

private const val ICON_CACHE_ENTRIES = 128
private const val ICON_TAG = "AppTile"

// ponytail: count-bounded (not byte-bounded) cache; icons are small. Key includes the server so a re-login elsewhere cannot show stale icons.
private val iconCache = LruCache<String, ImageBitmap>(ICON_CACHE_ENTRIES)

private suspend fun loadIcon(api: RelumeApi, path: String): ImageBitmap? {
    val key = api.url.resolve(path).toString()
    iconCache.get(key)?.let { return it }
    val bmp = try {
        val bytes = api.bytes(path)
        withContext(Dispatchers.Default) { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }
            ?: run { Log.w(ICON_TAG, "icon $path: ${bytes.size} bytes are not a decodable image"); return null }
    } catch (e: ApiError) {
        Log.w(ICON_TAG, "icon $path: HTTP ${e.status}: ${e.message}")
        return null
    } catch (e: IOException) {
        Log.w(ICON_TAG, "icon $path: ${e.javaClass.simpleName}: ${e.message}")
        return null
    }
    return bmp.asImageBitmap().also { iconCache.put(key, it) }
}

internal fun initials(label: String): String {
    val words = label.split(' ', '-', '_').filter { it.isNotEmpty() }
    return when {
        words.isEmpty() -> "?"
        words.size == 1 -> words[0].take(2)
        else -> "" + words[0][0] + words[1][0]
    }
}
