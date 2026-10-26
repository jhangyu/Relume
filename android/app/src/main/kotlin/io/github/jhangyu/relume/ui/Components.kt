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
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
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
import androidx.compose.foundation.text.BasicTextField
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
import androidx.compose.runtime.CompositionLocalProvider
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.layout
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
private val PILL_RADIUS = 8.dp
private val PILL_PADDING = 8.dp
private val ORIGIN_RADIUS = 4.dp
private val ORIGIN_HEIGHT = 20.dp
private val ORIGIN_PADDING = 6.dp
/** Pill and tag height (m3.css `.st`, `.tag`). */
val PillHeight = 24.dp
/** Pill and tag height inside a list row (m3.css `.li .st`, `.li .tag`). */
val ListPillHeight = 20.dp
private val LIST_LEADING = 16.dp
/** MD3 list item trailing inset; an inset group uses [GROUP_TRAILING] (m3.css `.li`, `.group > .li`). */
private val LIST_TRAILING = 24.dp
private val GROUP_TRAILING = 16.dp
private val ONE_LINE_ROW = 56.dp
private val TWO_LINE_ROW = 72.dp
private val SEARCH_BAR_HEIGHT = 56.dp
private val CHIP_DOT = 10.dp
private val CHIP_DOT_BOX = 18.dp
private const val CATEGORY_CHIP_BORDER_MIX = 0.7f
private const val TILE_CORNER_RATIO = 0.3f
private const val TILE_FONT_RATIO = 0.36f
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

/** Coloured label: the shape, height and inset differ between pills (m3.css `.st`) and origin labels (`.origin`). */
@Composable
private fun Label(text: String, container: Color, onContainer: Color, @DrawableRes icon: Int?, radius: Dp, height: Dp, inset: Dp) {
    Row(
        Modifier.heightIn(min = height).clip(RoundedCornerShape(radius)).background(container).padding(horizontal = inset),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) Glyph(icon, onContainer, PILL_ICON_SIZE)
        Txt(text, style = MaterialTheme.typography.labelMedium, color = onContainer, maxLines = 1)
    }
}

/** Pastel pill and tag: 8 dp corner, 8 dp side inset, [PillHeight] (use [ListPillHeight] inside list rows). */
@Composable
fun Pill(text: String, container: Color, onContainer: Color, @DrawableRes icon: Int? = null, height: Dp = PillHeight) {
    Label(text, container, onContainer, icon, PILL_RADIUS, height, PILL_PADDING)
}

/** Build status of an enabled app (`status.state`). */
@Composable
fun StatusPill(state: String, height: Dp = PillHeight) {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    when (state) {
        "up_to_date" -> Pill("Up to date", r.green.container, r.green.onContainer, R.drawable.ms_check, height)
        "update_available" -> Pill("New version", r.lavender.container, r.lavender.onContainer, height = height)
        "building" -> Pill("Building", r.blue.container, r.blue.onContainer, R.drawable.ms_progress_activity, height)
        "queued" -> Pill("Queued", r.blue.container, r.blue.onContainer, height = height)
        "needs_download_url" -> Pill("Needs URL", r.amber.container, r.amber.onContainer, R.drawable.ms_link, height)
        "failed" -> Pill("Failed", s.errorContainer, s.onErrorContainer, R.drawable.ms_error, height)
        "not_built" -> Pill("Not built", s.surfaceContainerHigh, s.onSurfaceVariant, height = height)
        else -> throw IllegalArgumentException("status.state: expected one of up_to_date, update_available, building, queued, failed, not_built, needs_download_url, got '$state'")
    }
}

/** Bundle origin: OFFICIAL = blue, BUILT-IN = teal, CUSTOM = pink; 4 dp corner, 20 dp high (m3.css `.origin`). */
@Composable
fun OriginLabel(origin: String) {
    val r = MaterialTheme.relume
    val (text, pastel) = when (origin) {
        "official" -> "OFFICIAL" to r.blue
        "builtin" -> "BUILT-IN" to r.teal
        "custom" -> "CUSTOM" to r.pink
        else -> throw IllegalArgumentException("origin: expected official, builtin or custom, got '$origin'")
    }
    Label(text, pastel.container, pastel.onContainer, null, ORIGIN_RADIUS, ORIGIN_HEIGHT, ORIGIN_PADDING)
}

/** Category hues of tokens.md section 3. */
internal enum class CategoryHue { Social, Video, Music, Productivity, Tools, Personalization, Maps, News }

/**
 * Store category name -> hue, the same patterns and order as the web's `catSlug` (local/webui/static/app.js CATS),
 * so "Social & Communication" is social and "Tools & Utilities" is tools. The web's lifestyle hue has no Android token.
 */
private val CATEGORY_PATTERNS = listOf(
    CategoryHue.Social to "social|communicat",
    CategoryHue.Video to "video|photo|entertain",
    CategoryHue.Music to "music|audio",
    CategoryHue.Productivity to "product|business|finance|education",
    CategoryHue.Tools to "tool|util",
    CategoryHue.Personalization to "personal",
    CategoryHue.Maps to "map|navig|travel",
    CategoryHue.News to "news|media|book",
).map { (hue, pattern) -> hue to Regex(pattern, RegexOption.IGNORE_CASE) }

/** Hue of a store category; null when the name matches none (m3.css `[data-cat]` default: neutral grey). */
internal fun categoryHue(category: String?): CategoryHue? =
    category?.let { name -> CATEGORY_PATTERNS.firstOrNull { (_, re) -> re.containsMatchIn(name) }?.first }

/** Category colours: first = container and glyph, second = gradient end. Unknown or missing = neutral grey. */
@Composable
internal fun categoryPastels(category: String?): Pair<Pastel, Pastel> {
    val r = MaterialTheme.relume
    val s = MaterialTheme.colorScheme
    return when (categoryHue(category)) {
        CategoryHue.Social -> r.pink to r.lavender
        CategoryHue.Video -> r.coral to r.amber
        CategoryHue.Music -> r.amber to r.coral
        CategoryHue.Tools -> r.teal to r.blue
        CategoryHue.Productivity -> r.blue to r.teal
        CategoryHue.Personalization -> r.lavender to r.pink
        CategoryHue.Maps -> r.green to r.teal
        CategoryHue.News -> r.blue to r.lavender
        null -> Pastel(s.surfaceContainerHigh, s.onSurfaceVariant) to Pastel(s.surfaceContainerHighest, s.onSurfaceVariant)
    }
}

@Composable
fun CategoryTag(category: String?, height: Dp = PillHeight) {
    if (category == null) return
    val (first, _) = categoryPastels(category)
    Pill(category, first.container, first.onContainer, height = height)
}

/** Trailing inset of list rows: 24 dp on the screen, 16 dp inside an [InsetGroup]. */
private val LocalListTrailing = staticCompositionLocalOf { LIST_TRAILING }

/** MD3 inset list group: white container, 16 dp radius. Only for choices such as the bundle list (tokens.md section 5). */
@Composable
fun InsetGroup(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(modifier.fillMaxWidth(), shape = RoundedCornerShape(INSET_GROUP_RADIUS), color = MaterialTheme.colorScheme.surfaceContainerLowest) {
        CompositionLocalProvider(LocalListTrailing provides GROUP_TRAILING) { Column(content = content) }
    }
}

/**
 * Lets a plain list run edge to edge inside a parent padded by [inset] on both sides: list rows carry their own
 * 16 dp insets (m3.css `.li`), so their text lines up with the parent's other content. Needs a bounded width.
 */
fun Modifier.fullBleed(inset: Dp = ScreenPadding): Modifier = layout { measurable, constraints ->
    check(constraints.hasBoundedWidth) { "fullBleed: expected a bounded parent width, got $constraints" }
    val extra = inset.roundToPx() * 2
    val placeable = measurable.measure(constraints.copy(minWidth = constraints.minWidth + extra, maxWidth = constraints.maxWidth + extra))
    layout(placeable.width - extra, placeable.height) { placeable.place(-extra / 2, 0) }
}

/** Section header (m3.css `.sec-label`): 48 dp, text on the content edge. */
@Composable
fun SectionHeader(text: String) {
    Box(Modifier.fillMaxWidth().height(TouchTarget), contentAlignment = Alignment.CenterStart) {
        Txt(text, style = MaterialTheme.typography.titleSmall, color = muted())
    }
}

enum class ButtonKind { Primary, Tonal, Outlined, Text, Install, Danger }

/** m3.css `.btn.small` (32 dp, in cards and rows), `.btn` (40 dp), `.btn.large` (48 dp, 16 dp corner, full-width bottom action). */
enum class ButtonSize { Small, Medium, Large }

private val SMALL_BUTTON_HEIGHT = 32.dp
private val SMALL_BUTTON_PADDING = PaddingValues(horizontal = 14.dp)
private val SMALL_BUTTON_ICON_GAP = 6.dp
private val LARGE_BUTTON_HEIGHT = 48.dp
private val LARGE_BUTTON_PADDING = PaddingValues(horizontal = 24.dp)
private val LARGE_BUTTON_RADIUS = 16.dp
private val BUTTON_ICON_GAP = 8.dp
private val BUTTON_ICON_SIZE = 18.dp

/** Button of the Relume palette: violet = build, sky = choose, coral = send to the phone (Install, Update). */
@Composable
fun ActionButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: ButtonKind = ButtonKind.Primary,
    enabled: Boolean = true,
    @DrawableRes icon: Int? = null,
    size: ButtonSize = ButtonSize.Medium,
) {
    val s = MaterialTheme.colorScheme
    val textStyle = if (size == ButtonSize.Large) LargeButtonText else MaterialTheme.typography.labelLarge
    val label: @Composable RowScope.() -> Unit = {
        if (icon != null) {
            Glyph(icon, contentOf(kind), BUTTON_ICON_SIZE)
            Spacer(Modifier.width(if (size == ButtonSize.Small) SMALL_BUTTON_ICON_GAP else BUTTON_ICON_GAP))
        }
        Text(text, maxLines = 1, overflow = TextOverflow.Ellipsis, style = textStyle)
    }
    val sized = when (size) {
        ButtonSize.Small -> modifier.height(SMALL_BUTTON_HEIGHT)
        ButtonSize.Medium -> modifier
        ButtonSize.Large -> modifier.height(LARGE_BUTTON_HEIGHT)
    }
    val shape = if (size == ButtonSize.Large) RoundedCornerShape(LARGE_BUTTON_RADIUS) else ButtonDefaults.shape
    val padding = when (size) {
        ButtonSize.Small -> SMALL_BUTTON_PADDING
        ButtonSize.Medium -> if (kind == ButtonKind.Text) ButtonDefaults.TextButtonContentPadding else ButtonDefaults.ContentPadding
        ButtonSize.Large -> LARGE_BUTTON_PADDING
    }
    when (kind) {
        ButtonKind.Primary -> Button(onClick, sized, enabled, shape = shape, contentPadding = padding, content = label)
        ButtonKind.Install -> Button(onClick, sized, enabled, shape = shape, colors = ButtonDefaults.buttonColors(containerColor = s.tertiary, contentColor = s.onTertiary), contentPadding = padding, content = label)
        ButtonKind.Danger -> Button(onClick, sized, enabled, shape = shape, colors = ButtonDefaults.buttonColors(containerColor = s.error, contentColor = s.onError), contentPadding = padding, content = label)
        ButtonKind.Tonal -> Button(onClick, sized, enabled, shape = shape, colors = ButtonDefaults.buttonColors(containerColor = s.secondaryContainer, contentColor = s.onSecondaryContainer), contentPadding = padding, content = label)
        ButtonKind.Outlined -> OutlinedButton(
            onClick, sized, enabled,
            shape = shape,
            colors = ButtonDefaults.outlinedButtonColors(contentColor = s.secondary),
            border = BorderStroke(1.dp, if (enabled) s.secondary else s.secondary.copy(alpha = DISABLED_ALPHA)),
            contentPadding = padding,
            content = label,
        )
        ButtonKind.Text -> TextButton(onClick, sized, enabled, shape = shape, colors = ButtonDefaults.textButtonColors(contentColor = s.secondary), contentPadding = padding, content = label)
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
            Modifier.size(32.dp).clip(CircleShape).background(MaterialTheme.relume.lavender.container)
                .semantics { contentDescription = "Phone settings and account" }
                .clickable(role = Role.Button, onClick = account.openSettings),
            contentAlignment = Alignment.Center,
        ) {
            Txt(account.initials, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.relume.lavender.onContainer)
        }
    }
}

/**
 * Filter chips with 16 dp side insets; the selected one is secondaryContainer with a check. [wrap] = flow onto more
 * lines (inside a card) instead of scrolling sideways. [categories] (one per label, null = neutral) gives a chip the
 * category look of tokens.md section 2: gradient dot, on-container label, border = on-container at 70 % over white,
 * category container when selected.
 */
@Composable
fun ChipRow(
    labels: List<String>,
    selected: Int,
    wrap: Boolean = false,
    categories: List<String?> = emptyList(),
    onSelect: (Int) -> Unit,
) {
    require(categories.isEmpty() || categories.size == labels.size) {
        "ChipRow: expected ${labels.size} categories (one per label) or none, got ${categories.size}"
    }
    val chips: @Composable () -> Unit = {
        labels.forEachIndexed { i, label -> RelumeChip(label, i == selected, categories.getOrNull(i)) { onSelect(i) } }
    }
    val gap = Arrangement.spacedBy(8.dp)
    val padding = Modifier.fillMaxWidth().padding(horizontal = ScreenPadding, vertical = 8.dp)
    if (wrap) {
        FlowRow(padding, horizontalArrangement = gap, verticalArrangement = gap) { chips() }
    } else {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = ScreenPadding, vertical = 8.dp), horizontalArrangement = gap) { chips() }
    }
}

@Composable
private fun RelumeChip(label: String, selected: Boolean, category: String?, onClick: () -> Unit) {
    val s = MaterialTheme.colorScheme
    if (category == null) {
        FilterChip(
            selected = selected,
            onClick = onClick,
            label = { Text(label, maxLines = 1) },
            leadingIcon = if (selected) ({ Glyph(R.drawable.ms_check, s.onSecondaryContainer, BUTTON_ICON_SIZE) }) else null,
            colors = FilterChipDefaults.filterChipColors(
                selectedContainerColor = s.secondaryContainer,
                selectedLabelColor = s.onSecondaryContainer,
            ),
            border = FilterChipDefaults.filterChipBorder(
                enabled = true,
                selected = selected,
                borderColor = s.outline,
                selectedBorderColor = Color.Transparent,
            ),
        )
        return
    }
    val (first, second) = categoryPastels(category)
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = { Text(label, maxLines = 1) },
        leadingIcon = {
            Box(Modifier.size(CHIP_DOT_BOX), contentAlignment = Alignment.Center) {
                Box(Modifier.size(CHIP_DOT).clip(CircleShape).background(Brush.linearGradient(listOf(first.onContainer, second.container))))
            }
        },
        colors = FilterChipDefaults.filterChipColors(
            labelColor = first.onContainer,
            selectedContainerColor = first.container,
            selectedLabelColor = first.onContainer,
        ),
        border = FilterChipDefaults.filterChipBorder(
            enabled = true,
            selected = selected,
            borderColor = lerp(s.surfaceContainerLowest, first.onContainer, CATEGORY_CHIP_BORDER_MIX),
            selectedBorderColor = Color.Transparent,
        ),
    )
}

/**
 * MD3 search bar (m3.css `.searchbar`): 56 dp, fully rounded, surfaceContainerHigh fill, no border, search glyph on
 * the 16 dp inset, [placeholder] while empty, optional [trailing] 48 dp icon button.
 */
@Composable
fun SearchBar(
    value: String,
    onChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    trailing: @Composable (() -> Unit)? = null,
) {
    val s = MaterialTheme.colorScheme
    val text = MaterialTheme.typography.bodyLarge
    BasicTextField(
        value = value,
        onValueChange = onChange,
        modifier = modifier.fillMaxWidth(),
        singleLine = true,
        textStyle = text.copy(color = s.onSurface),
        cursorBrush = SolidColor(s.secondary),
        decorationBox = { field ->
            Row(
                Modifier.fillMaxWidth().height(SEARCH_BAR_HEIGHT).clip(RoundedCornerShape(50)).background(s.surfaceContainerHigh)
                    .padding(start = LIST_LEADING, end = if (trailing == null) LIST_LEADING else 4.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Glyph(R.drawable.ms_search)
                Box(Modifier.weight(1f), contentAlignment = Alignment.CenterStart) {
                    if (value.isEmpty()) Txt(placeholder, style = text, color = s.onSurfaceVariant, maxLines = 1)
                    field()
                }
                trailing?.invoke()
            }
        },
    )
}

/** Two-line list row: leading slot, headline, supporting text, trailing slot; min 56/72 dp, insets 16 / 24 dp. */
@Composable
fun ListRow(
    headline: String,
    modifier: Modifier = Modifier,
    supporting: String? = null,
    leading: @Composable (() -> Unit)? = null,
    trailing: @Composable (() -> Unit)? = null,
) {
    Row(
        modifier.fillMaxWidth().heightIn(min = if (supporting == null) ONE_LINE_ROW else TWO_LINE_ROW)
            .padding(start = LIST_LEADING, end = LocalListTrailing.current, top = 8.dp, bottom = 8.dp),
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

/** Settings-style row: title and help text on the left, switch on the right; the whole row toggles. 56 / 72 dp. */
@Composable
fun SwitchRow(title: String, help: String?, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = if (help == null) ONE_LINE_ROW else TWO_LINE_ROW)
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange)
            .padding(start = LIST_LEADING, end = LocalListTrailing.current, top = 12.dp, bottom = 12.dp),
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

/** Two-line radio row (radio, name and supporting line, trailing label): 72 dp, the whole row is the target. */
@Composable
fun RadioRow(selected: Boolean, enabled: Boolean = true, onSelect: () -> Unit, content: @Composable RowScope.() -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = TWO_LINE_ROW)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onSelect)
            .padding(start = LIST_LEADING, end = LocalListTrailing.current, top = 8.dp, bottom = 8.dp),
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
 * App tile (m3.css `.tile`): corner 0.3 x size, two-container gradient of the app's [category] (neutral grey when
 * unknown). Shows the server icon at [iconUrl] when there is one, the initials while it loads, when [iconUrl] is null
 * and when the fetch fails (logged with package path and status).
 */
@Composable
fun AppTile(label: String, iconUrl: String?, api: RelumeApi?, size: Dp = 40.dp, category: String? = null) {
    val (first, second) = categoryPastels(category)
    val icon by produceState<ImageBitmap?>(null, iconUrl, api) { value = if (iconUrl != null && api != null) loadIcon(api, iconUrl) else null }
    Box(
        Modifier.size(size).clip(RoundedCornerShape(size * TILE_CORNER_RATIO)).background(Brush.linearGradient(listOf(first.container, second.container))),
        contentAlignment = Alignment.Center,
    ) {
        val bmp = icon
        if (bmp != null) {
            Image(bmp, contentDescription = label, modifier = Modifier.size(size))
        } else {
            Txt(initials(label), style = TextStyle(fontSize = (size.value * TILE_FONT_RATIO).sp, fontWeight = FontWeight.SemiBold), color = first.onContainer)
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
