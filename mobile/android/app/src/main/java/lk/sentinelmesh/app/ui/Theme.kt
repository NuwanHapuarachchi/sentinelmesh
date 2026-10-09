package lk.sentinelmesh.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Same palette as the officers' console, so the whole system looks like one service.
object C {
    val mist = Color(0xFFE9EFF1)
    val paper = Color(0xFFFFFFFF)
    val ink = Color(0xFF12324A)
    val inkSoft = Color(0xFF4D6676)
    val river = Color(0xFF2F6F8F)
    val rule = Color(0xFFCFDBE0)
    val normal = Color(0xFFC9D6DC)
    val watch = Color(0xFFE7C84B)
    val advisory = Color(0xFFE8913A)
    val warning = Color(0xFFD14B3D)
    val imminent = Color(0xFF7D1220)
    val blind = Color(0xFF6B5CA5)
    val clear = Color(0xFF2E7D4F)
    val live = Color(0xFF2E9E5B)
}

fun tierColor(tier: String?, blind: Boolean = false): Color = if (blind) C.blind else when (tier) {
    "watch" -> C.watch
    "advisory" -> C.advisory
    "warning" -> C.warning
    "imminent" -> C.imminent
    "clear" -> C.clear
    else -> C.normal
}

/** Text that reads on top of the tier colour. */
fun onTier(tier: String?, blind: Boolean = false): Color =
    if (blind || tier in listOf("warning", "imminent", "clear")) Color.White else C.ink

val TIER_ORDER = listOf("normal", "watch", "advisory", "warning", "imminent")

private val type = Typography(
    displaySmall = TextStyle(fontSize = 34.sp, fontWeight = FontWeight.Black, lineHeight = 40.sp, letterSpacing = (-0.5).sp),
    headlineSmall = TextStyle(fontSize = 24.sp, fontWeight = FontWeight.Bold, lineHeight = 30.sp),
    titleLarge = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.Bold, lineHeight = 26.sp),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.SemiBold, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    labelLarge = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.2.sp),
    labelMedium = TextStyle(fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.6.sp),
)

@Composable
fun SentinelTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = C.river, onPrimary = Color.White, secondary = C.ink, background = C.mist, surface = C.paper,
            onSurface = C.ink, onBackground = C.ink, surfaceVariant = C.mist, onSurfaceVariant = C.inkSoft, outline = C.rule,
            secondaryContainer = Color(0xFFD6E6EE), onSecondaryContainer = C.ink,
        ),
        typography = type,
        content = content,
    )
}

@Composable
fun Card(modifier: Modifier = Modifier, padding: Dp = 18.dp, content: @Composable ColumnScope.() -> Unit) {
    Surface(modifier = modifier, shape = RoundedCornerShape(22.dp), color = C.paper, shadowElevation = 1.dp) {
        Column(Modifier.padding(padding), content = content)
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(text.uppercase(), modifier = modifier, style = MaterialTheme.typography.labelMedium, color = C.inkSoft)
}

@Composable
fun Dot(color: Color, size: Dp = 10.dp) {
    Box(Modifier.size(size).clip(CircleShape).background(color))
}
