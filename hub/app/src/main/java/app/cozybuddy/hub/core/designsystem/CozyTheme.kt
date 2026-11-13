package app.cozybuddy.hub.core.designsystem

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf

private val LocalClockColors = staticCompositionLocalOf { DarkClockColors }

object CozyTheme {
    val clockColors: ClockColors
        @Composable
        @ReadOnlyComposable
        get() = LocalClockColors.current
}

/**
 * 허브 공통 테마.
 *
 * 허브는 방에 상시 켜 두는 화면이라 기본값이 다크다. 라이트는 휴대폰 앱과 디자인 시스템을
 * 공유할 때를 위한 짝이다.
 */
@Composable
fun CozyTheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit,
) {
    val colorScheme = if (darkTheme) CozyDarkColorScheme else CozyLightColorScheme
    val clockColors = if (darkTheme) DarkClockColors else LightClockColors
    CompositionLocalProvider(LocalClockColors provides clockColors) {
        MaterialTheme(
            colorScheme = colorScheme,
            typography = CozyTypography,
            content = content,
        )
    }
}
