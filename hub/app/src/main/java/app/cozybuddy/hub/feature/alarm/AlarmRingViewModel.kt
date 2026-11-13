package app.cozybuddy.hub.feature.alarm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.domain.scheduling.SnoozePolicy
import app.cozybuddy.hub.feature.ambient.ClockDisplayConfig
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn

@HiltViewModel
class AlarmRingViewModel @Inject constructor(
    holder: RingingStateHolder,
    private val control: AlarmRingControl,
    private val timeSource: TimeSource,
    private val displayConfig: ClockDisplayConfig,
) : ViewModel() {

    val uiState: StateFlow<AlarmRingUiState> = holder.state
        .map { state -> state.toUi() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), AlarmRingUiState())

    fun onStop() = control.stop()

    fun onSnooze() = control.snooze()

    private fun RingingState.toUi(): AlarmRingUiState = when (this) {
        RingingState.Idle -> AlarmRingUiState()
        is RingingState.Ringing -> AlarmRingUiState(
            visible = true,
            kind = kind,
            label = label,
            timeText = formatEpoch(startedAtEpochMs, timeSource.now().zone, displayConfig.is24Hour(), displayConfig.locale()),
            snoozeMinutes = SnoozePolicy.DURATION.toMinutes().toInt(),
        )
    }

    private companion object {
        const val STOP_TIMEOUT_MS = 5_000L
    }
}
