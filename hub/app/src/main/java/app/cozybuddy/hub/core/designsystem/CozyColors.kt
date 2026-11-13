package app.cozybuddy.hub.core.designsystem

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color

internal val CozyDarkColorScheme: ColorScheme = darkColorScheme(
    primary = Color(0xFFD9A06B),
    onPrimary = Color(0xFF1A1208),
    background = Color(0xFF101010),
    onBackground = Color(0xFFF4F4F2),
    surface = Color(0xFF1A1A1A),
    onSurface = Color(0xFFF4F4F2),
    surfaceVariant = Color(0xFF242424),
    onSurfaceVariant = Color(0xFFB8BEC8),
)

internal val CozyLightColorScheme: ColorScheme = lightColorScheme(
    primary = Color(0xFF9A5B1F),
    onPrimary = Color(0xFFFFFFFF),
    background = Color(0xFFF6F3EE),
    onBackground = Color(0xFF1A1A1A),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1A1A1A),
    surfaceVariant = Color(0xFFE9E4DC),
    onSurfaceVariant = Color(0xFF4A4A48),
)

/** 플립 시계 전용 팔레트. Material 슬롯에 대응하지 않는 카드 질감 색을 모은다. */
data class ClockColors(
    val backgroundCenter: Color,
    val backgroundMid: Color,
    val backgroundEdge: Color,
    val cardTopStart: Color,
    val cardTopEnd: Color,
    val cardBottomStart: Color,
    val cardBottomEnd: Color,
    val digit: Color,
    val seam: Color,
    val colonDot: Color,
    val caption: Color,
)

internal val DarkClockColors = ClockColors(
    backgroundCenter = Color(0xFF1A1A1A),
    backgroundMid = Color(0xFF101010),
    backgroundEdge = Color(0xFF0A0A0A),
    cardTopStart = Color(0xFF242424),
    cardTopEnd = Color(0xFF1D1D1D),
    cardBottomStart = Color(0xFF181818),
    cardBottomEnd = Color(0xFF141414),
    digit = Color(0xFFF4F4F2),
    seam = Color(0xA6000000),
    colonDot = Color(0xFF3A3A3A),
    caption = Color(0xFFFFFFFF),
)

internal val LightClockColors = ClockColors(
    backgroundCenter = Color(0xFFFBF9F5),
    backgroundMid = Color(0xFFF6F3EE),
    backgroundEdge = Color(0xFFEAE5DC),
    cardTopStart = Color(0xFFFFFFFF),
    cardTopEnd = Color(0xFFF3F0EA),
    cardBottomStart = Color(0xFFEAE6DE),
    cardBottomEnd = Color(0xFFE2DDD4),
    digit = Color(0xFF1E1E1C),
    seam = Color(0x33000000),
    colonDot = Color(0xFFC9C4BA),
    caption = Color(0xFF1A1A1A),
)
