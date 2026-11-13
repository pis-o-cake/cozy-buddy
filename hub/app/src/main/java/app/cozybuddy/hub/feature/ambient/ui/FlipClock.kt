package app.cozybuddy.hub.feature.ambient.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.cozybuddy.hub.core.designsystem.ClockColors
import app.cozybuddy.hub.core.designsystem.ClockDigitFamily
import app.cozybuddy.hub.core.designsystem.CozyTheme

// 치수는 목업(1280 폭 컨테이너 기준 cqi)을 그대로 옮긴 비율이다. unit 은 화면 폭의 1%.
private const val CARD_WIDTH = 18f
private const val CARD_HEIGHT = 28f
private const val CARD_GAP = 1.9f
private const val CARD_RADIUS = 2.7f
private const val CARD_INNER_RADIUS = 0.5f
private const val DIGIT_FONT_SIZE = 22.5f
private const val SEAM_HEIGHT = 0.35f
private const val COLON_DOT = 1.7f
private const val COLON_GAP = 3.8f
private const val COLON_MARGIN = 0.7f
private const val CAMERA_DISTANCE = 78f

// 숫자 글리프 높이를 em 의 약 0.7 로 보고 카드 중앙에 맞춘다. 광학 보정은 목업의 -1.1cqi 와 같다.
private const val DIGIT_GLYPH_HALF_EM = 0.35f
private const val DIGIT_OPTICAL_SHIFT = -1.1f

private const val FLIP_DURATION_MS = 330
private const val FLIP_SPLIT = 0.45f
private const val FLIP_FALL_DEGREES = 90f

@Composable
fun FlipClock(
    digits: List<Int>,
    unit: Dp,
    modifier: Modifier = Modifier,
) {
    val colors = CozyTheme.clockColors
    val measurer = rememberTextMeasurer(cacheSize = DIGIT_CACHE_SIZE)
    val digitStyle = TextStyle(
        fontFamily = ClockDigitFamily,
        fontWeight = FontWeight.Normal,
        fontSize = with(LocalDensity.current) { (unit * DIGIT_FONT_SIZE).toSp() },
        fontFeatureSettings = "tnum",
    )

    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(unit * CARD_GAP),
    ) {
        FlipDigit(digits[0], unit, colors, measurer, digitStyle)
        FlipDigit(digits[1], unit, colors, measurer, digitStyle)
        Colon(unit, colors)
        FlipDigit(digits[2], unit, colors, measurer, digitStyle)
        FlipDigit(digits[3], unit, colors, measurer, digitStyle)
    }
}

@Composable
private fun Colon(unit: Dp, colors: ClockColors) {
    Column(
        modifier = Modifier.padding(horizontal = unit * COLON_MARGIN),
        verticalArrangement = Arrangement.spacedBy(unit * COLON_GAP),
    ) {
        repeat(2) {
            Box(
                Modifier
                    .size(unit * COLON_DOT)
                    .background(colors.colonDot, CircleShape),
            )
        }
    }
}

private enum class Half { Top, Bottom }

@Composable
private fun FlipDigit(
    digit: Int,
    unit: Dp,
    colors: ClockColors,
    measurer: TextMeasurer,
    style: TextStyle,
) {
    // previous 는 애니메이션이 끝난 뒤에만 갱신한다. 그 사이 digit != previous 가 "넘어가는 중"의 표지다.
    var previous by remember { mutableIntStateOf(digit) }
    val progress = remember(digit) { Animatable(if (digit == previous) 1f else 0f) }

    LaunchedEffect(digit) {
        if (digit == previous) return@LaunchedEffect
        progress.animateTo(1f, tween(FLIP_DURATION_MS, easing = LinearEasing))
        previous = digit
    }

    val flipping = digit != previous
    val p = progress.value
    val cameraDistancePx = with(LocalDensity.current) { (unit * CAMERA_DISTANCE).toPx() }

    Box(Modifier.size(unit * CARD_WIDTH, unit * CARD_HEIGHT)) {
        // 정적 면: 위는 항상 새 값, 아래는 아래 플랩이 닫히기 전까지 옛 값.
        HalfCard(Half.Top, digit, unit, colors, measurer, style, Modifier.align(Alignment.TopCenter))
        HalfCard(
            Half.Bottom,
            if (flipping && p < 1f) previous else digit,
            unit, colors, measurer, style,
            Modifier.align(Alignment.BottomCenter),
        )

        if (flipping && p < FLIP_SPLIT) {
            val fall = p / FLIP_SPLIT
            HalfCard(
                Half.Top, previous, unit, colors, measurer, style,
                Modifier
                    .align(Alignment.TopCenter)
                    .graphicsLayer {
                        rotationX = -FLIP_FALL_DEGREES * fall
                        transformOrigin = TransformOrigin(0.5f, 1f)
                        cameraDistance = cameraDistancePx
                    },
            )
        } else if (flipping) {
            val rise = (p - FLIP_SPLIT) / (1f - FLIP_SPLIT)
            HalfCard(
                Half.Bottom, digit, unit, colors, measurer, style,
                Modifier
                    .align(Alignment.BottomCenter)
                    .graphicsLayer {
                        rotationX = FLIP_FALL_DEGREES * (1f - rise)
                        transformOrigin = TransformOrigin(0.5f, 0f)
                        cameraDistance = cameraDistancePx
                    },
            )
        }

        Box(
            Modifier
                .align(Alignment.Center)
                .size(unit * CARD_WIDTH, unit * SEAM_HEIGHT)
                .background(colors.seam),
        )
    }
}

@Composable
private fun HalfCard(
    half: Half,
    digit: Int,
    unit: Dp,
    colors: ClockColors,
    measurer: TextMeasurer,
    style: TextStyle,
    modifier: Modifier = Modifier,
) {
    val outer = unit * CARD_RADIUS
    val inner = unit * CARD_INNER_RADIUS
    val shape = if (half == Half.Top) {
        RoundedCornerShape(outer, outer, inner, inner)
    } else {
        RoundedCornerShape(inner, inner, outer, outer)
    }
    val brush = if (half == Half.Top) {
        Brush.verticalGradient(listOf(colors.cardTopStart, colors.cardTopEnd))
    } else {
        Brush.verticalGradient(listOf(colors.cardBottomStart, colors.cardBottomEnd))
    }
    val text = digit.toString()
    val layout: TextLayoutResult = remember(text, style) { measurer.measure(text, style) }

    Canvas(
        modifier
            .size(unit * CARD_WIDTH, unit * CARD_HEIGHT / 2)
            .clip(shape)
            .background(brush),
    ) {
        val cardHeight = size.height * 2
        val baseline = cardHeight / 2 +
            DIGIT_GLYPH_HALF_EM * style.fontSize.toPx() +
            DIGIT_OPTICAL_SHIFT * unit.toPx()
        val topOfFullCard = baseline - layout.firstBaseline
        val y = if (half == Half.Top) topOfFullCard else topOfFullCard - size.height
        drawText(
            textLayoutResult = layout,
            color = colors.digit,
            topLeft = Offset((size.width - layout.size.width) / 2f, y),
        )
    }
}

private const val DIGIT_CACHE_SIZE = 12
