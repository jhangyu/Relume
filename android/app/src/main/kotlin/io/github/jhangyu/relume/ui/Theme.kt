package io.github.jhangyu.relume.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Fluent 2 tokens of fluent-alt-NOTES.md / spec 10; light and dark ramps. */
@Immutable
class FluentColors(
    val bg: Color, val bg2: Color, val card: Color,
    val stroke: Color, val stroke2: Color,
    val fg: Color, val fg2: Color, val fg3: Color,
    val accent: Color, val onAccent: Color,
    val ok: Color, val warn: Color, val danger: Color, val info: Color,
    val dark: Boolean,
)

private val LightColors = FluentColors(
    bg = Color(0xFFFAFAFA), bg2 = Color(0xFFFFFFFF), card = Color(0xFFFFFFFF),
    stroke = Color(0xFFE0E0E0), stroke2 = Color(0xFFD1D1D1),
    fg = Color(0xFF242424), fg2 = Color(0xFF424242), fg3 = Color(0xFF707070),
    accent = Color(0xFF0E7A56), onAccent = Color(0xFFFFFFFF),
    ok = Color(0xFF107C10), warn = Color(0xFFBC4B09), danger = Color(0xFFB10E1C), info = Color(0xFF0F6CBD),
    dark = false,
)

private val DarkColors = FluentColors(
    bg = Color(0xFF141414), bg2 = Color(0xFF1F1F1F), card = Color(0xFF292929),
    stroke = Color(0xFF3D3D3D), stroke2 = Color(0xFF525252),
    fg = Color(0xFFFFFFFF), fg2 = Color(0xFFD6D6D6), fg3 = Color(0xFFA3A3A3),
    accent = Color(0xFF3DBF8D), onAccent = Color(0xFF06231A),
    ok = Color(0xFF54B054), warn = Color(0xFFFAA06B), danger = Color(0xFFDC626D), info = Color(0xFF479EF5),
    dark = true,
)

/** Radii 4 (controls), 8 (cards), 12 (dialogs). */
object FluentShapes {
    val control = RoundedCornerShape(4.dp)
    val card = RoundedCornerShape(8.dp)
    val dialog = RoundedCornerShape(12.dp)
}

/** Body 14 sp, title 20 sp semibold, versions monospace. */
object FluentType {
    val body = TextStyle(fontSize = 14.sp, lineHeight = 20.sp)
    val small = TextStyle(fontSize = 12.sp, lineHeight = 16.sp)
    val label = TextStyle(fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    val title = TextStyle(fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.SemiBold)
    val headline = TextStyle(fontSize = 26.sp, fontWeight = FontWeight.Bold)
    val mono = TextStyle(fontSize = 13.sp, fontFamily = FontFamily.Monospace)
    val monoSmall = TextStyle(fontSize = 11.5.sp, lineHeight = 19.sp, fontFamily = FontFamily.Monospace)
}

private val LocalFluentColors = staticCompositionLocalOf<FluentColors> { error("FluentTheme is missing above this composable") }

object FluentTheme {
    val colors: FluentColors
        @Composable @ReadOnlyComposable get() = LocalFluentColors.current
}

/** Material3 is used only for neutral shells (pull-to-refresh); its scheme is mapped from the Fluent tokens. */
@Composable
fun FluentTheme(content: @Composable () -> Unit) {
    val c = if (isSystemInDarkTheme()) DarkColors else LightColors
    val scheme = if (c.dark) {
        darkColorScheme(primary = c.accent, onPrimary = c.onAccent, surface = c.card, onSurface = c.fg, background = c.bg)
    } else {
        lightColorScheme(primary = c.accent, onPrimary = c.onAccent, surface = c.card, onSurface = c.fg, background = c.bg)
    }
    CompositionLocalProvider(LocalFluentColors provides c) {
        MaterialTheme(colorScheme = scheme, content = content)
    }
}
