package app.cozybuddy.hub.feature.conversation.ui

import androidx.activity.compose.LocalActivity
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.core.designsystem.CozyTheme
import app.cozybuddy.hub.domain.voice.AssistantPhase
import app.cozybuddy.hub.feature.conversation.ConversationConfig
import app.cozybuddy.hub.feature.conversation.ConversationNotice
import app.cozybuddy.hub.feature.conversation.ConversationUiState
import app.cozybuddy.hub.feature.conversation.ConversationViewModel

private const val SCRIM_ALPHA = 0.94f
private const val MIC_BUTTON_ALPHA = 0.45f
private const val NIGHT_SAFE_MIN_BRIGHTNESS = 0.4f
private const val THINKING_DOT_COUNT = 3
private const val THINKING_PERIOD_MS = 900
private const val WAVE_BAR_WIDTH_DP = 6f
private const val WAVE_BAR_GAP_DP = 6f
private const val WAVE_MIN_FRACTION = 0.06f

/**
 * 앰비언트(와 그 위의 모든 화면) 위에 얹는 대화 오버레이와, 앰비언트 구석의 마이크 버튼.
 *
 * 평소에는 터치를 가로채지 않는다. 대화가 있는 동안만 전체를 덮는다. 알람 울림 오버레이보다 아래에 그려야 한다.
 */
@Composable
fun ConversationOverlayRoute(
    viewModel: ConversationViewModel,
    showMicButton: Boolean,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    BrightenWhileVisible(state.visible)
    Box(modifier.fillMaxSize()) {
        if (showMicButton && state.micEnabled && !state.visible) {
            MicButton(
                onClick = viewModel::onMicTap,
                modifier = Modifier.align(Alignment.BottomStart).padding(24.dp),
            )
        }
        ConversationOverlay(state, onTap = viewModel::onOverlayTap)
    }
}

@Composable
internal fun MicButton(onClick: () -> Unit, modifier: Modifier = Modifier) {
    IconButton(
        onClick = onClick,
        modifier = modifier
            .size(56.dp)
            .alpha(MIC_BUTTON_ALPHA),
    ) {
        Icon(
            painter = painterResource(R.drawable.ic_stat_mic),
            contentDescription = stringResource(R.string.conversation_mic_button),
            tint = CozyTheme.clockColors.caption,
            modifier = Modifier.size(32.dp),
        )
    }
}

@Composable
fun ConversationOverlay(state: ConversationUiState, onTap: () -> Unit, modifier: Modifier = Modifier) {
    AnimatedVisibility(visible = state.visible, enter = fadeIn(), exit = fadeOut(), modifier = modifier) {
        Column(
            Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.background.copy(alpha = SCRIM_ALPHA))
                .pointerInput(Unit) { detectTapGestures { onTap() } }
                .padding(horizontal = 64.dp, vertical = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            StatusLabel(state)
            ConversationBody(state)
            PhaseIndicator(state, Modifier.padding(top = 32.dp))
        }
    }
}

@Composable
private fun StatusLabel(state: ConversationUiState) {
    val label = when (state.phase) {
        AssistantPhase.LISTENING -> stringResource(R.string.conversation_listening)
        AssistantPhase.THINKING -> stringResource(R.string.conversation_thinking)
        AssistantPhase.SPEAKING -> stringResource(R.string.conversation_speaking_hint)
        AssistantPhase.IDLE -> noticeText(state.notice)
    }
    if (label.isEmpty()) return
    Text(
        text = label,
        style = MaterialTheme.typography.titleMedium,
        color = MaterialTheme.colorScheme.primary,
        textAlign = TextAlign.Center,
    )
}

@Composable
private fun ConversationBody(state: ConversationUiState) {
    if (state.transcript.isNotEmpty()) {
        Text(
            text = state.transcript,
            style = MaterialTheme.typography.headlineSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 24.dp),
        )
    }
    if (state.response.isNotEmpty()) {
        Text(
            text = state.response,
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.onBackground,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 24.dp),
        )
    }
}

@Composable
private fun PhaseIndicator(state: ConversationUiState, modifier: Modifier = Modifier) {
    when (state.phase) {
        AssistantPhase.LISTENING -> LevelWaveform(state.levels, modifier)
        AssistantPhase.THINKING -> ThinkingDots(modifier)
        else -> Unit
    }
}

@Composable
private fun noticeText(notice: ConversationNotice): String = when (notice) {
    ConversationNotice.NONE -> ""
    ConversationNotice.OFFLINE -> stringResource(R.string.conversation_notice_offline)
    ConversationNotice.MIC_UNAVAILABLE -> stringResource(R.string.conversation_notice_mic_unavailable)
    ConversationNotice.ERROR -> stringResource(R.string.conversation_notice_error)
}

@Composable
private fun LevelWaveform(levels: List<Float>, modifier: Modifier = Modifier) {
    val color = MaterialTheme.colorScheme.primary
    val description = stringResource(R.string.conversation_level_description)
    Canvas(
        modifier
            .fillMaxWidth()
            .height(72.dp)
            .semantics { contentDescription = description },
    ) {
        val barWidth = WAVE_BAR_WIDTH_DP.dp.toPx()
        val gap = WAVE_BAR_GAP_DP.dp.toPx()
        val slots = ConversationConfig.LEVEL_HISTORY
        val totalWidth = slots * barWidth + (slots - 1) * gap
        val startX = (size.width - totalWidth) / 2f
        val padded = List(slots - levels.size) { 0f } + levels
        padded.forEachIndexed { i, level ->
            val barHeight = size.height * level.coerceIn(WAVE_MIN_FRACTION, 1f)
            drawRoundRect(
                color = color,
                topLeft = Offset(startX + i * (barWidth + gap), (size.height - barHeight) / 2f),
                size = Size(barWidth, barHeight),
                cornerRadius = CornerRadius(barWidth / 2f),
            )
        }
    }
}

@Composable
private fun ThinkingDots(modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "thinking")
    Row(modifier.height(72.dp), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(THINKING_DOT_COUNT) { index ->
            val alpha by transition.animateFloat(
                initialValue = 0.25f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(THINKING_PERIOD_MS / 2, delayMillis = index * THINKING_PERIOD_MS / 6, easing = LinearEasing),
                    repeatMode = RepeatMode.Reverse,
                ),
                label = "dot$index",
            )
            Box(
                Modifier
                    .size(14.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primary.copy(alpha = alpha)),
            )
        }
    }
}

/**
 * 야간 디밍으로 창 밝기가 매우 낮을 때 오버레이가 보이는 동안만 밝기를 올린다.
 * 시스템 기본 밝기(음수)는 그대로 둔다.
 */
@Composable
private fun BrightenWhileVisible(visible: Boolean) {
    val window = LocalActivity.current?.window ?: return
    DisposableEffect(window, visible) {
        if (!visible) return@DisposableEffect onDispose { }
        val previous = window.attributes.screenBrightness
        val raised = previous in 0f..<NIGHT_SAFE_MIN_BRIGHTNESS
        if (raised) {
            window.attributes = window.attributes.also { it.screenBrightness = NIGHT_SAFE_MIN_BRIGHTNESS }
        }
        onDispose {
            if (raised) window.attributes = window.attributes.also { it.screenBrightness = previous }
        }
    }
}
