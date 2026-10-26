package io.github.jhangyu.relume.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** One fixed light scheme (android/tokens.md sections 1 and 2): no dynamic colour, no dark scheme. */
val RelumeLight = lightColorScheme(
    primary = Color(0xFF6E5FD4), onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFE6E1FF), onPrimaryContainer = Color(0xFF34248C),
    secondary = Color(0xFF2F6EAE), onSecondary = Color(0xFFFFFFFF),
    secondaryContainer = Color(0xFFDBE9FF), onSecondaryContainer = Color(0xFF0B3D91),
    tertiary = Color(0xFFB85A35), onTertiary = Color(0xFFFFFFFF),
    tertiaryContainer = Color(0xFFFFE1D5), onTertiaryContainer = Color(0xFF7D2A0E),
    error = Color(0xFFBA1A1A), onError = Color(0xFFFFFFFF),
    errorContainer = Color(0xFFFFDAD6), onErrorContainer = Color(0xFF93000A),
    background = Color(0xFFF8F8F8), onBackground = Color(0xFF1C1C1C),
    surface = Color(0xFFF8F8F8), onSurface = Color(0xFF1C1C1C),
    surfaceVariant = Color(0xFFE8E8E8), onSurfaceVariant = Color(0xFF474747),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF3F3F3),
    surfaceContainer = Color(0xFFEEEEEE),
    surfaceContainerHigh = Color(0xFFE8E8E8),
    surfaceContainerHighest = Color(0xFFE2E2E2),
    outline = Color(0xFF767676), outlineVariant = Color(0xFFC8C8C8),
    inverseSurface = Color(0xFF303030), inverseOnSurface = Color(0xFFF1F1F1),
)

/** A pastel container with the dark text colour that sits on it (tokens.md section 3). */
@Immutable
class Pastel(val container: Color, val onContainer: Color)

/** Extended roles of tokens.md section 3 and the per-tab navigation indicators. */
@Immutable
class RelumeColors(
    val blue: Pastel = Pastel(Color(0xFFDBE9FF), Color(0xFF0B3D91)),
    val green: Pastel = Pastel(Color(0xFFD4F3DC), Color(0xFF14532D)),
    val amber: Pastel = Pastel(Color(0xFFFFEDC2), Color(0xFF6B4500)),
    val coral: Pastel = Pastel(Color(0xFFFFE1D6), Color(0xFF8A2C0D)),
    val pink: Pastel = Pastel(Color(0xFFFDE0EF), Color(0xFF8A1650)),
    val lavender: Pastel = Pastel(Color(0xFFE8E0FF), Color(0xFF4A2A8F)),
    val teal: Pastel = Pastel(Color(0xFFD0F2EE), Color(0xFF0F5249)),
    /** Lifestyle category (the web's gold-olive lifestyle hue): 7.8:1, and 7.6:1 on the coral gradient end. */
    val olive: Pastel = Pastel(Color(0xFFF0EDC0), Color(0xFF4D4800)),
    /** Glyph colours by meaning: build, sync and search, add link. */
    val buildGlyph: Color = Color(0xFF34248C),
    val syncGlyph: Color = Color(0xFF0B3D91),
    val linkGlyph: Color = Color(0xFF6B4500),
)

val LocalRelumeColors = staticCompositionLocalOf { RelumeColors() }

/** `MaterialTheme.relume.green` and so on. */
val MaterialTheme.relume: RelumeColors
    @Composable @ReadOnlyComposable get() = LocalRelumeColors.current

/** Every role one step down the MD3 scale (tokens.md section 4); components keep their default slot. */
private fun style(size: Int, line: Int, weight: FontWeight, tracking: Double) =
    TextStyle(fontSize = size.sp, lineHeight = line.sp, fontWeight = weight, letterSpacing = tracking.sp)

val RelumeTypography = Typography(
    headlineSmall = style(22, 28, FontWeight.Normal, 0.0),
    titleLarge = style(16, 24, FontWeight.Medium, 0.15),
    titleMedium = style(14, 20, FontWeight.Medium, 0.1),
    titleSmall = style(12, 16, FontWeight.Medium, 0.5),
    bodyLarge = style(14, 20, FontWeight.Normal, 0.25),
    bodyMedium = style(12, 16, FontWeight.Normal, 0.4),
    bodySmall = style(11, 16, FontWeight.Normal, 0.4),
    labelLarge = style(12, 16, FontWeight.Medium, 0.5),
    labelMedium = style(11, 16, FontWeight.Medium, 0.5),
    labelSmall = style(11, 16, FontWeight.Medium, 0.5),
)

/** Full-width bottom action (m3.css `.btn.large`, tokens.md section 4): 14 sp. */
val LargeButtonText = style(14, 20, FontWeight.Medium, 0.1)

/** Monospace slot for versions, tags and log lines. */
val MonoSmall = TextStyle(fontSize = 11.sp, lineHeight = 16.sp, fontFamily = FontFamily.Monospace)

@Composable
fun RelumeTheme(content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalRelumeColors provides RelumeColors()) {
        MaterialTheme(colorScheme = RelumeLight, typography = RelumeTypography, content = content)
    }
}
