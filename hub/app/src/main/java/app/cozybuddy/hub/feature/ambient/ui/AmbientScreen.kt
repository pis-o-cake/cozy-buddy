package app.cozybuddy.hub.feature.ambient.ui

import androidx.activity.compose.LocalActivity
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.core.designsystem.CozyTheme
import app.cozybuddy.hub.feature.ambient.AmbientConfig
import app.cozybuddy.hub.feature.ambient.AmbientUiState
import app.cozybuddy.hub.feature.ambient.AmbientWeather
import app.cozybuddy.hub.feature.ambient.AmbientViewModel
import app.cozybuddy.hub.feature.ambient.ConnectionIndicator
import app.cozybuddy.hub.feature.ambient.PixelShift

private const val DIM_TRANSITION_MS = 1_100
private const val SHIFT_TRANSITION_MS = 3_000
private const val ASPECT_RATIO = 1.6f
private const val DATE_FONT_SIZE = 2.3f
private const val DATE_BOTTOM_PADDING = 3.4f
private const val DATE_ALPHA = 0.6f
private const val WEATHER_FONT_SIZE = 1.9f
private const val WEATHER_BOTTOM_PADDING = 7.4f
private const val WEATHER_ALPHA = 0.45f
private const val BACKGROUND_CENTER_Y = 0.42f
private const val BACKGROUND_MID_STOP = 0.55f
private const val INDICATOR_FONT_SIZE = 1.6f
private const val INDICATOR_EDGE_PADDING = 2.2f
private const val INDICATOR_ALPHA = 0.4f
private const val SWIPE_OPEN_THRESHOLD_PX = 160f

@Composable
fun AmbientRoute(viewModel: AmbientViewModel, onLongPress: () -> Unit = {}, onSwipeLeft: () -> Unit = {}) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val indicator by viewModel.connectionIndicator.collectAsStateWithLifecycle()
    WindowBrightnessEffect(state.dim.windowBrightness)
    AmbientScreen(state, indicator = indicator, onLongPress = onLongPress, onSwipeLeft = onSwipeLeft)
}

@Composable
fun AmbientScreen(
    state: AmbientUiState,
    modifier: Modifier = Modifier,
    indicator: ConnectionIndicator = ConnectionIndicator.NONE,
    onLongPress: () -> Unit = {},
    onSwipeLeft: () -> Unit = {},
) {
    val colors = CozyTheme.clockColors
    val contentAlpha by animateFloatAsState(
        targetValue = state.dim.contentAlpha,
        animationSpec = tween(DIM_TRANSITION_MS),
        label = "contentAlpha",
    )
    val shift = state.pixelShift.animated()

    BoxWithConstraints(
        modifier
            .fillMaxSize()
            .pointerInput(onLongPress) { detectTapGestures(onLongPress = { onLongPress() }) }
            .pointerInput(onSwipeLeft) {
                var total = 0f
                detectHorizontalDragGestures(
                    onDragStart = { total = 0f },
                    onDragEnd = { if (total < -SWIPE_OPEN_THRESHOLD_PX) onSwipeLeft() },
                    onDragCancel = { total = 0f },
                ) { _, delta -> total += delta }
            }
            .drawBehind {
                val radius = size.maxDimension * 0.6f
                drawRect(
                    Brush.radialGradient(
                        0f to colors.backgroundCenter,
                        BACKGROUND_MID_STOP to colors.backgroundMid,
                        1f to colors.backgroundEdge,
                        center = Offset(size.width / 2f, size.height * BACKGROUND_CENTER_Y),
                        radius = radius,
                    ),
                )
            },
    ) {
        // 모든 치수는 화면 폭 1% 단위. 폭이 남아도 높이에 맞춰 줄어 세로 화면에서도 잘리지 않는다.
        val unit = minOf(maxWidth, maxHeight * ASPECT_RATIO) / 100
        val timeDescription = stringResource(
            R.string.ambient_time_description,
            state.digits.take(2).joinToString("") + ":" + state.digits.drop(2).joinToString(""),
        )

        Box(
            Modifier
                .fillMaxSize()
                .offset(shift.first, shift.second)
                .graphicsLayer { alpha = contentAlpha },
        ) {
            FlipClock(
                digits = state.digits,
                unit = unit,
                modifier = Modifier
                    .align(Alignment.Center)
                    .semantics { contentDescription = timeDescription },
            )
            Text(
                text = state.dateText,
                color = colors.caption.copy(alpha = DATE_ALPHA),
                style = MaterialTheme.typography.bodyLarge.copy(
                    fontSize = with(LocalDensity.current) { (unit * DATE_FONT_SIZE).toSp() },
                ),
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = unit * DATE_BOTTOM_PADDING),
            )
            state.weather?.let { weather ->
                WeatherLine(
                    weather = weather,
                    unit = unit,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = unit * WEATHER_BOTTOM_PADDING),
                )
            }
            ConnectionIndicatorLabel(
                indicator = indicator,
                unit = unit,
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .padding(end = unit * INDICATOR_EDGE_PADDING, bottom = unit * INDICATOR_EDGE_PADDING),
            )
        }
    }
}

/** 날짜 줄 바로 위의 한 줄. 날짜보다 작고 흐리게 그려 시계를 방해하지 않는다. */
@Composable
private fun WeatherLine(weather: AmbientWeather, unit: Dp, modifier: Modifier = Modifier) {
    Text(
        text = stringResource(
            R.string.ambient_weather_line,
            stringResource(weather.condition.label),
            weather.temperature,
            weather.max,
            weather.min,
        ),
        color = CozyTheme.clockColors.caption.copy(alpha = WEATHER_ALPHA),
        style = MaterialTheme.typography.bodyMedium.copy(
            fontSize = with(LocalDensity.current) { (unit * WEATHER_FONT_SIZE).toSp() },
        ),
        modifier = modifier,
    )
}

/** 작고 어두운 글자만 쓴다. 야간에도 눈에 띄지 않되 상태를 알 수 있어야 한다. */
@Composable
private fun ConnectionIndicatorLabel(indicator: ConnectionIndicator, unit: Dp, modifier: Modifier = Modifier) {
    val text = when (indicator) {
        ConnectionIndicator.NONE -> return
        ConnectionIndicator.OFFLINE -> stringResource(R.string.connection_indicator_offline)
        ConnectionIndicator.AUTH_FAILED -> stringResource(R.string.connection_indicator_auth_failed)
    }
    Text(
        text = text,
        color = CozyTheme.clockColors.caption.copy(alpha = INDICATOR_ALPHA),
        style = MaterialTheme.typography.bodySmall.copy(
            fontSize = with(LocalDensity.current) { (unit * INDICATOR_FONT_SIZE).toSp() },
        ),
        modifier = modifier,
    )
}

@Composable
private fun PixelShift.animated(): Pair<Dp, Dp> {
    val x by animateDpAsState(xDp.dp, tween(SHIFT_TRANSITION_MS), label = "shiftX")
    val y by animateDpAsState(yDp.dp, tween(SHIFT_TRANSITION_MS), label = "shiftY")
    return x to y
}

/** 야간 디밍 시 창 밝기를 낮추고, 화면을 벗어나면 시스템 값으로 되돌린다. */
@Composable
private fun WindowBrightnessEffect(brightness: Float) {
    val window = LocalActivity.current?.window ?: return
    DisposableEffect(window, brightness) {
        val params = window.attributes
        params.screenBrightness = brightness
        window.attributes = params
        onDispose {
            val reset = window.attributes
            reset.screenBrightness = AmbientConfig.BRIGHTNESS_SYSTEM
            window.attributes = reset
        }
    }
}
