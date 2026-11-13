package app.cozybuddy.hub.feature.ambient

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.weather.WeatherRepository
import app.cozybuddy.hub.domain.model.Weather
import dagger.hilt.android.lifecycle.HiltViewModel
import java.time.ZonedDateTime
import javax.inject.Inject
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn

@HiltViewModel
class AmbientViewModel @Inject constructor(
    private val timeSource: TimeSource,
    private val displayConfig: ClockDisplayConfig,
    link: HubLink,
    private val weather: WeatherRepository,
) : ViewModel() {

    private val minuteTicks: Flow<ZonedDateTime> = flow {
        while (true) {
            val now = timeSource.now()
            emit(now)
            delay(millisUntilNextMinute(now.toInstant().toEpochMilli()))
        }
    }

    // 날씨가 바뀌거나 분이 바뀔 때마다 다시 만든다. 오래된 관측은 분 틱에서 걸러진다.
    val uiState: StateFlow<AmbientUiState> = combine(minuteTicks, weather.state, ::snapshot)
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS),
            initialValue = snapshot(timeSource.now(), weather.state.value),
        )

    /**
     * 서버 연결 표시. 부팅 직후나 짧은 재연결 중에 시계 화면이 깜빡이지 않도록 끊김은 [OFFLINE_GRACE_MS] 뒤에 알린다.
     * 연결 시도(Connecting)와 대기(Disconnected)는 같은 "끊김"으로 묶어, 재시도 주기가 유예 시간을 되돌리지 않게 한다.
     */
    @OptIn(ExperimentalCoroutinesApi::class)
    val connectionIndicator: StateFlow<ConnectionIndicator> = link.state
        .map { state ->
            when (state) {
                is ConnectionState.Connected -> ConnectionIndicator.NONE
                ConnectionState.AuthFailed -> ConnectionIndicator.AUTH_FAILED
                ConnectionState.Connecting, ConnectionState.Disconnected -> ConnectionIndicator.OFFLINE
            }
        }
        .distinctUntilChanged()
        .flatMapLatest { target ->
            if (target != ConnectionIndicator.OFFLINE) {
                flowOf(target)
            } else {
                flow {
                    delay(OFFLINE_GRACE_MS)
                    emit(target)
                }
            }
        }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), ConnectionIndicator.NONE)

    private fun snapshot(now: ZonedDateTime, weather: Weather?): AmbientUiState = buildAmbientState(
        now = now,
        is24Hour = displayConfig.is24Hour(),
        locale = displayConfig.locale(),
        datePattern = displayConfig.datePattern(),
        weather = weather,
    )

    private companion object {
        const val STOP_TIMEOUT_MS = 5_000L
        const val OFFLINE_GRACE_MS = 10_000L
    }
}

// 폴링하지 않고 다음 분 경계까지 한 번만 잠든다.
// TODO: 사용자가 시각·시간대를 바꾸면 최대 1분 늦게 반영된다. ACTION_TIME_CHANGED 수신으로 보완한다.
internal fun millisUntilNextMinute(epochMillis: Long): Long =
    MILLIS_PER_MINUTE - Math.floorMod(epochMillis, MILLIS_PER_MINUTE)
