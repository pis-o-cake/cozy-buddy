package app.cozybuddy.hub.core.designsystem

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import app.cozybuddy.hub.R

/** 본문·자막용 한글 겸용 서체. */
val PretendardFamily = FontFamily(
    Font(R.font.pretendard_regular, FontWeight.Normal),
    Font(R.font.pretendard_medium, FontWeight.Medium),
)

/** 시계 숫자 전용 서체. 숫자만 쓰므로 Regular 한 벌만 번들한다. */
val ClockDigitFamily = FontFamily(
    Font(R.font.selawik_regular, FontWeight.Normal),
)

private fun TextStyle.withPretendard(): TextStyle = copy(fontFamily = PretendardFamily)

private val BaseTypography = Typography()

internal val CozyTypography = Typography(
    displayLarge = BaseTypography.displayLarge.withPretendard(),
    displayMedium = BaseTypography.displayMedium.withPretendard(),
    displaySmall = BaseTypography.displaySmall.withPretendard(),
    headlineLarge = BaseTypography.headlineLarge.withPretendard(),
    headlineMedium = BaseTypography.headlineMedium.withPretendard(),
    headlineSmall = BaseTypography.headlineSmall.withPretendard(),
    titleLarge = BaseTypography.titleLarge.withPretendard(),
    titleMedium = BaseTypography.titleMedium.withPretendard(),
    titleSmall = BaseTypography.titleSmall.withPretendard(),
    bodyLarge = BaseTypography.bodyLarge.withPretendard(),
    bodyMedium = BaseTypography.bodyMedium.withPretendard(),
    bodySmall = BaseTypography.bodySmall.withPretendard(),
    labelLarge = BaseTypography.labelLarge.withPretendard(),
    labelMedium = BaseTypography.labelMedium.withPretendard(),
    labelSmall = BaseTypography.labelSmall.withPretendard(),
)
