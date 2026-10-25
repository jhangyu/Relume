package io.github.jhangyu.relume.ui

import android.graphics.BitmapFactory
import android.util.Log
import android.util.LruCache
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.runtime.produceState
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.RelumeApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.selection.toggleable
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog

/** Smallest touch target (spec 10, user requirement). */
val TouchTarget = 48.dp
private const val ICON_VIEWPORT = 24f
private const val ICON_STROKE = 1.75f
private const val TINT_FILL = 0.14f
private const val TINT_BORDER = 0.35f
private const val SWITCH_ON_ALPHA = 1f

fun Color.tint(alpha: Float) = copy(alpha = alpha)

@Composable
fun Txt(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = FluentType.body,
    color: Color = FluentTheme.colors.fg,
    maxLines: Int = Int.MAX_VALUE,
    bold: Boolean = false,
) {
    BasicText(
        text = text,
        modifier = modifier,
        style = style.copy(color = color, fontWeight = if (bold) FontWeight.SemiBold else style.fontWeight),
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )
}

/** Fluent line icons: the 24x24 stroke paths of the mockup. */
object Icons {
    const val Wifi = "M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5v.1"
    const val Search = "M11 18a7 7 0 1 0 0-14a7 7 0 0 0 0 14zM20 20l-4-4"
    const val Refresh = "M20 11a8 8 0 1 0-2.3 6.2M20 4v7h-7"
    const val Back = "M15 6l-6 6 6 6"
    const val Close = "M6 6l12 12M18 6L6 18"
    const val Apps = "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"
    const val Builds = "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5"
    const val Settings = "M4 7h9M17 7h3M4 17h3M11 17h9M15 4v6M9 14v6"
    const val Play = "M8 5l11 7-11 7z"
    const val Warning = "M12 4l9.5 16.5h-19zM12 10v4M12 17.2v.1"
    const val Box = "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10"
    const val Server = "M4 5h16v5H4zM4 14h16v5H4zM8 7.5v.1M8 16.5v.1"
    const val User = "M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8zM5 20a7 7 0 0 1 14 0"
    const val Lock = "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3"
    const val Eye = "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6a3 3 0 0 0 0 6z"
    const val Check = "M5 12.5l4.5 4.5L19 7.5"
    const val Phone = "M8 2h8a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2"
}

@Composable
fun Ic(path: String, tint: Color = FluentTheme.colors.fg2, size: Dp = 20.dp) {
    val parsed = remember(path) { PathParser().parsePathString(path).toPath() }
    Canvas(Modifier.size(size)) {
        scale(scaleX = this.size.width / ICON_VIEWPORT, scaleY = this.size.height / ICON_VIEWPORT, pivot = Offset.Zero) {
            drawPath(parsed, tint, style = Stroke(ICON_STROKE, cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
    }
}

@Composable
fun IconButton(path: String, description: String, onClick: () -> Unit, tint: Color = FluentTheme.colors.fg2) {
    Box(
        Modifier
            .size(TouchTarget)
            .clip(CircleShape)
            .semantics { contentDescription = description }
            .clickable(role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Ic(path, tint)
    }
}

/** Tinted pill badge. */
@Composable
fun Chip(text: String, color: Color) {
    Txt(
        text,
        Modifier
            .clip(RoundedCornerShape(50))
            .background(color.tint(TINT_FILL))
            .border(1.dp, color.tint(TINT_BORDER), RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 2.dp),
        style = FluentType.small,
        color = color,
        bold = true,
        maxLines = 1,
    )
}

@Composable
fun FCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val c = FluentTheme.colors
    Column(
        modifier.fillMaxWidth().clip(FluentShapes.card).background(c.card).border(1.dp, c.stroke, FluentShapes.card),
        content = content,
    )
}

enum class ButtonKind { Default, Primary, Danger, DangerText }

@Composable
fun FButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: ButtonKind = ButtonKind.Default,
    compact: Boolean = false,
    enabled: Boolean = true,
    subtitle: String? = null,
    icon: String? = null,
) {
    val c = FluentTheme.colors
    val (fill, fg, stroke) = when (kind) {
        ButtonKind.Default -> Triple(c.bg, c.fg, c.stroke2)
        ButtonKind.Primary -> Triple(c.accent, c.onAccent, c.accent)
        ButtonKind.Danger -> Triple(c.danger, Color.White, c.danger)
        ButtonKind.DangerText -> Triple(c.bg, c.danger, c.stroke2)
    }
    val alpha = if (enabled) 1f else DISABLED_ALPHA
    Row(
        modifier
            .heightIn(min = if (compact) TouchTarget else if (subtitle != null) 56.dp else TouchTarget)
            .clip(FluentShapes.control)
            .background(fill.tint(alpha))
            .border(1.dp, stroke.tint(alpha), FluentShapes.control)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = if (compact) 12.dp else 16.dp, vertical = if (subtitle != null) 8.dp else 0.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) Ic(icon, fg.tint(alpha), 18.dp)
        if (subtitle == null) {
            Txt(text, style = if (compact) FluentType.label else FluentType.body, color = fg.tint(alpha), bold = true, maxLines = 1)
        } else {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Txt(text, color = fg.tint(alpha), bold = true)
                Txt(subtitle, style = FluentType.small, color = fg.tint(alpha * SUBTITLE_ALPHA))
            }
        }
    }
}

private const val DISABLED_ALPHA = 0.45f
private const val SUBTITLE_ALPHA = 0.85f

private val SWITCH_WIDTH = 40.dp
private val SWITCH_HEIGHT = 22.dp
private val THUMB = 13.dp
private val THUMB_INSET = 3.dp

/** Outlined 40x22 switch. Draw only; pair with [SwitchRow] or [ToggleLabel] for the 48 dp hit target. */
@Composable
fun SwitchGlyph(checked: Boolean) {
    val c = FluentTheme.colors
    Box(
        Modifier
            .size(SWITCH_WIDTH, SWITCH_HEIGHT)
            .clip(RoundedCornerShape(SWITCH_HEIGHT / 2))
            .background(if (checked) c.accent.tint(SWITCH_ON_ALPHA) else Color.Transparent)
            .border(1.5.dp, if (checked) c.accent else c.fg3, RoundedCornerShape(SWITCH_HEIGHT / 2)),
    ) {
        Box(
            Modifier
                .align(Alignment.CenterStart)
                .offset(x = if (checked) SWITCH_WIDTH - THUMB - THUMB_INSET - 3.dp else THUMB_INSET)
                .size(THUMB)
                .clip(CircleShape)
                .background(if (checked) c.onAccent else c.fg3),
        )
    }
}

/** Inline switch with label; the whole row is the toggle target (min 48 dp tall). */
@Composable
fun ToggleLabel(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    val c = FluentTheme.colors
    Row(
        Modifier
            .heightIn(min = TouchTarget)
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SwitchGlyph(checked)
        Txt(label, style = FluentType.label.copy(fontWeight = FontWeight.Normal), color = if (enabled) c.fg2 else c.fg3)
    }
}

/** Settings-style row: title and help text on the left, switch on the right. */
@Composable
fun SwitchRow(title: String, help: String, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Txt(title, bold = true)
            Txt(help, style = FluentType.small, color = FluentTheme.colors.fg3)
        }
        SwitchGlyph(checked)
    }
}

enum class Tone { Info, Ok, Danger, Warn }

@Composable
private fun Tone.color(): Color = with(FluentTheme.colors) {
    when (this@color) {
        Tone.Info -> info
        Tone.Ok -> ok
        Tone.Danger -> danger
        Tone.Warn -> warn
    }
}

/** Message bar: tinted strip with icon, bold headline and optional detail and action link. */
@Composable
fun MessageBar(
    headline: String,
    tone: Tone,
    modifier: Modifier = Modifier,
    detail: String? = null,
    icon: String = if (tone == Tone.Ok || tone == Tone.Info) Icons.Wifi else Icons.Warning,
    action: String? = null,
    onAction: () -> Unit = {},
) {
    val tint = tone.color()
    val c = FluentTheme.colors
    Row(
        modifier
            .fillMaxWidth()
            .clip(FluentShapes.control)
            .background(tint.tint(0.12f))
            .border(1.dp, tint.tint(0.35f), FluentShapes.control)
            .padding(12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Ic(icon, tint, 16.dp)
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Txt(headline, style = FluentType.label, bold = true)
            if (detail != null) Txt(detail, style = FluentType.small, color = c.fg2)
            if (action != null) {
                Txt(
                    action,
                    Modifier.heightIn(min = TouchTarget).clickable(role = Role.Button, onClick = onAction).padding(top = 12.dp),
                    style = FluentType.label,
                    color = c.accent,
                    bold = true,
                )
            }
        }
    }
}

/** Pivot tabs: underline marks the selected one. */
@Composable
fun Pivot(labels: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    val c = FluentTheme.colors
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
        labels.forEachIndexed { i, label ->
            Box(
                Modifier
                    .heightIn(min = TouchTarget)
                    .clickable(role = Role.Tab) { onSelect(i) }
                    .padding(horizontal = 12.dp),
                contentAlignment = Alignment.Center,
            ) {
                Txt(label, style = FluentType.label, color = if (i == selected) c.fg else c.fg3, bold = true, maxLines = 1)
                if (i == selected) {
                    Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(3.dp).clip(RoundedCornerShape(2.dp)).background(c.accent))
                }
            }
        }
    }
    Box(Modifier.fillMaxWidth().height(1.dp).background(c.stroke))
}

data class NavItem(val label: String, val icon: String)

/** Bottom navigation with a tinted pill behind the selected icon. */
@Composable
fun BottomNav(items: List<NavItem>, selected: Int, onSelect: (Int) -> Unit) {
    val c = FluentTheme.colors
    Column(Modifier.fillMaxWidth().background(c.bg2)) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(c.stroke))
        Row(Modifier.fillMaxWidth()) {
            items.forEachIndexed { i, item ->
                val on = i == selected
                Column(
                    Modifier
                        .weight(1f)
                        .heightIn(min = TouchTarget)
                        .clickable(role = Role.Tab) { onSelect(i) }
                        .padding(top = 8.dp, bottom = 10.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Box(
                        Modifier.size(56.dp, 30.dp).clip(RoundedCornerShape(15.dp)).background(if (on) c.accent.tint(0.18f) else Color.Transparent),
                        contentAlignment = Alignment.Center,
                    ) { Ic(item.icon, if (on) c.accent else c.fg3) }
                    Txt(item.label, style = FluentType.small.copy(fontSize = 11.sp), color = if (on) c.fg else c.fg3, bold = on)
                }
            }
        }
    }
}

@Composable
fun AppBar(title: String, subtitle: String? = null, onBack: (() -> Unit)? = null, actions: @Composable RowScope.() -> Unit = {}) {
    Row(
        Modifier.fillMaxWidth().padding(start = if (onBack != null) 4.dp else 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (onBack != null) IconButton(Icons.Back, "Back", onBack)
        Column(Modifier.weight(1f)) {
            Txt(title, style = FluentType.title, maxLines = 1)
            if (subtitle != null) Txt(subtitle, style = FluentType.monoSmall.copy(fontSize = 11.sp, lineHeight = 14.sp), color = FluentTheme.colors.fg3, maxLines = 1)
        }
        actions()
    }
}

/** Labeled text field with Fluent look (4 dp corner, thick bottom stroke). */
@Composable
fun Field(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    icon: String? = null,
    mono: Boolean = false,
    secret: Boolean = false,
    error: String? = null,
    hint: String? = null,
    enabled: Boolean = true,
    keyboard: KeyboardOptions = KeyboardOptions.Default,
) {
    val c = FluentTheme.colors
    var reveal by remember { mutableStateOf(false) }
    val requester = remember { BringIntoViewRequester() }
    Column(modifier.bringIntoViewRequester(requester), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Txt(label, style = FluentType.label, bold = true)
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = TouchTarget)
                .clip(FluentShapes.control)
                .border(1.dp, if (error != null) c.danger else c.stroke2, FluentShapes.control)
                .background(c.bg)
                .padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (icon != null) Ic(icon, c.fg2)
            BasicTextField(
                value = value,
                onValueChange = onChange,
                enabled = enabled,
                singleLine = true,
                textStyle = (if (mono) FluentType.mono else FluentType.body).copy(color = c.fg),
                cursorBrush = SolidColor(c.accent),
                visualTransformation = if (secret && !reveal) PasswordVisualTransformation() else VisualTransformation.None,
                keyboardOptions = keyboard,
                modifier = Modifier.weight(1f),
            )
            if (secret) {
                Box(Modifier.size(TouchTarget).clickable(role = Role.Button) { reveal = !reveal }, contentAlignment = Alignment.Center) {
                    Ic(Icons.Eye, if (reveal) c.accent else c.fg3)
                }
            }
        }
        if (error != null) Txt(error, style = FluentType.small, color = c.danger)
        else if (hint != null) Txt(hint, style = FluentType.small, color = c.fg3)
    }
}

@Composable
fun CheckRow(text: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    val c = FluentTheme.colors
    Row(
        Modifier.fillMaxWidth().heightIn(min = TouchTarget).toggleable(value = checked, role = Role.Checkbox, onValueChange = onChange),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .size(20.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(if (checked) c.accent else Color.Transparent)
                .border(1.5.dp, if (checked) c.accent else c.fg3, RoundedCornerShape(4.dp)),
            contentAlignment = Alignment.Center,
        ) { if (checked) Ic(Icons.Check, c.onAccent, 14.dp) }
        Txt(text)
    }
}

/** Dialog shell: 12 dp corners, 24 dp padding, scrim from the platform dialog window. */
@Composable
fun FDialog(onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    val c = FluentTheme.colors
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.fillMaxWidth().clip(FluentShapes.dialog).background(c.bg).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
            content = content,
        )
    }
}

@Composable
fun DialogIcon(path: String, tone: Tone) {
    val tint = tone.color()
    Box(Modifier.size(44.dp).clip(CircleShape).background(tint.tint(0.15f)), contentAlignment = Alignment.Center) { Ic(path, tint) }
}

@Composable
fun DialogButtons(content: @Composable RowScope.() -> Unit) {
    Row(Modifier.fillMaxWidth().padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), content = content)
}

/**
 * Round app tile. Shows the server icon at [iconUrl] when there is one, the initials tile (colour derived from
 * the label) while it loads, when [iconUrl] is null and when the fetch fails (logged with package path and status).
 */
@Composable
fun AppTile(label: String, iconUrl: String?, api: RelumeApi?, size: Dp = 40.dp) {
    val hue = (label.hashCode().mod(TILE_HUES)) * (360f / TILE_HUES)
    val bg = Color.hsv(hue, 0.65f, 0.75f)
    val icon by produceState<ImageBitmap?>(null, iconUrl, api) { value = if (iconUrl != null && api != null) loadIcon(api, iconUrl) else null }
    Box(Modifier.size(size).clip(RoundedCornerShape(size / 4)).background(bg), contentAlignment = Alignment.Center) {
        val bmp = icon
        if (bmp != null) {
            Image(bmp, contentDescription = label, modifier = Modifier.size(size))
        } else {
            Txt(initials(label), style = FluentType.label.copy(fontSize = if (size > 48.dp) 18.sp else 13.sp), color = Color.White, bold = true)
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

private const val TILE_HUES = 12

internal fun initials(label: String): String {
    val words = label.split(' ', '-', '_').filter { it.isNotEmpty() }
    return when {
        words.isEmpty() -> "?"
        words.size == 1 -> words[0].take(2)
        else -> "" + words[0][0] + words[1][0]
    }
}

@Composable
fun Hairline(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(1.dp).background(FluentTheme.colors.stroke))
}

internal val ScreenPadding = 16.dp
