package app.cozybuddy.hub.feature.ambient

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.core.time.TimeSource
import dagger.hilt.android.lifecycle.HiltViewModel
import java.time.ZonedDateTime
import javax.inject.Inject
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.stateIn

@HiltViewModel
class AmbientViewModel @Inject constructor(
    private val timeSource: TimeSource,
    private val displayConfig: ClockDisplayConfig,
) : ViewModel() {

    val uiState: StateFlow<AmbientUiState> = flow {
        while (true) {
            val now = timeSource.now()
            emit(snapshot(now))
            delay(millisUntilNextMinute(now.toInstant().toEpochMilli()))
        }
    }.stateIn(
        scope = viewModelScope,
        started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS),
        initialValue = snapshot(timeSource.now()),
    )

    private fun snapshot(now: ZonedDateTime): AmbientUiState = buildAmbientState(
        now = now,
        is24Hour = displayConfig.is24Hour(),
        locale = displayConfig.locale(),
        datePattern = displayConfig.datePattern(),
    )

    private companion object {
        const val STOP_TIMEOUT_MS = 5_000L
    }
}

// 폴링하지 않고 다음 분 경계까지 한 번만 잠든다.
// TODO: 사용자가 시각·시간대를 바꾸면 최대 1분 늦게 반영된다. ACTION_TIME_CHANGED 수신으로 보완한다.
internal fun millisUntilNextMinute(epochMillis: Long): Long =
    MILLIS_PER_MINUTE - Math.floorMod(epochMillis, MILLIS_PER_MINUTE)
