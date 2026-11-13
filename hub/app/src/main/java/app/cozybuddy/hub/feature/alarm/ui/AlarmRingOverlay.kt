package app.cozybuddy.hub.feature.alarm.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.feature.alarm.AlarmRingUiState
import app.cozybuddy.hub.feature.alarm.AlarmRingViewModel

private const val TIME_FONT_SIZE = 140
private const val LABEL_FONT_SIZE = 36
private const val OVERLAY_ALPHA = 0.96f

/** 앰비언트를 포함한 모든 화면 위에 얹히는 울림 오버레이. 울리지 않으면 아무것도 그리지 않는다. */
@Composable
fun AlarmRingOverlayRoute(viewModel: AlarmRingViewModel, modifier: Modifier = Modifier) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    AlarmRingOverlay(state, viewModel::onStop, viewModel::onSnooze, modifier)
}

@Composable
fun AlarmRingOverlay(
    state: AlarmRingUiState,
    onStop: () -> Unit,
    onSnooze: () -> Unit,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(visible = state.visible, enter = fadeIn(), exit = fadeOut(), modifier = modifier) {
        Column(
            Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.background.copy(alpha = OVERLAY_ALPHA))
                // 아래 화면으로 터치가 새지 않게 막는다.
                .pointerInput(Unit) { detectTapGestures { } },
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = state.label.ifBlank { defaultLabel(state.kind) },
                style = MaterialTheme.typography.headlineMedium.copy(fontSize = LABEL_FONT_SIZE.sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = state.timeText,
                style = MaterialTheme.typography.displayLarge.copy(fontSize = TIME_FONT_SIZE.sp),
                color = MaterialTheme.colorScheme.onBackground,
            )
            Row(Modifier.padding(top = 40.dp), horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                OutlinedButton(onClick = onSnooze, modifier = Modifier.width(220.dp).height(72.dp)) {
                    Text(stringResource(R.string.alarm_ring_snooze, state.snoozeMinutes), fontSize = 22.sp)
                }
                Button(
                    onClick = onStop,
                    modifier = Modifier.width(220.dp).height(72.dp),
                    colors = ButtonDefaults.buttonColors(),
                ) {
                    Text(stringResource(R.string.alarm_ring_stop), fontSize = 22.sp)
                }
            }
        }
    }
}

@Composable
internal fun defaultLabel(kind: AlarmKind): String = stringResource(
    if (kind == AlarmKind.TIMER) R.string.alarm_default_label_timer else R.string.alarm_default_label,
)
