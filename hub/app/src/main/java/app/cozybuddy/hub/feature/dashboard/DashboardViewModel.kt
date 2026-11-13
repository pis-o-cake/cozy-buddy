package app.cozybuddy.hub.feature.dashboard

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.data.home.CommandFailure
import app.cozybuddy.hub.data.home.CommandOutcome
import app.cozybuddy.hub.data.home.DeviceCommandSender
import app.cozybuddy.hub.data.home.HomeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.runningFold
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import timber.log.Timber

@HiltViewModel
class DashboardViewModel @Inject constructor(
    private val home: HomeRepository,
    link: HubLink,
    private val sender: DeviceCommandSender,
) : ViewModel() {

    private val pending = MutableStateFlow<Set<String>>(emptySet())
    private val error = MutableStateFlow<CommandFailure?>(null)
    private val brightnessJobs = mutableMapOf<String, Job>()
    private var inactivityJob: Job? = null
    private val _returnToAmbient = Channel<Unit>(Channel.CONFLATED)

    /** 비활동 시간이 지나 앰비언트로 돌아가야 할 때마다 한 번 나온다. */
    val returnToAmbient: Flow<Unit> = _returnToAmbient.receiveAsFlow()

    // 연결이 끊겨도 허브가 놓인 구역은 그대로이므로 마지막으로 받은 값을 유지한다.
    private val hubAreaId: Flow<String?> = link.state
        .map { (it as? ConnectionState.Connected)?.hubInfo?.areaId?.takeIf(String::isNotBlank) }
        .runningFold<String?, String?>(null) { last, latest -> latest ?: last }

    val uiState: StateFlow<DashboardUiState> = combine(
        home.state,
        link.state,
        pending,
        error,
        hubAreaId,
    ) { homeState, connection, pendingIds, failure, hubArea ->
        DashboardUiState(
            connection = when (connection) {
                is ConnectionState.Connected -> DashboardConnection.CONNECTED
                ConnectionState.AuthFailed -> DashboardConnection.AUTH_FAILED
                ConnectionState.Connecting, ConnectionState.Disconnected -> DashboardConnection.OFFLINE
            },
            sections = buildSections(homeState, hubArea, pendingIds),
            error = failure,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), DashboardUiState())

    /** 켜기/끄기, 열기/닫기, 장면·스크립트 실행을 카드 종류에 맞춰 보낸다. */
    fun onPrimaryAction(entityId: String) {
        val entity = home.state.value.entities[entityId] ?: return
        if (entityId in pending.value) return
        val card = entity.toCard(pending = false)
        val action = when (card.kind) {
            CardKind.LIGHT, CardKind.TOGGLE, CardKind.COVER -> if (card.state == CardState.ON || card.state == CardState.OPEN) ACTION_OFF else ACTION_ON
            CardKind.SCENE -> ACTION_ACTIVATE
            CardKind.SCRIPT -> ACTION_RUN
            CardKind.READ_ONLY -> return
        }
        if (card.state == CardState.UNAVAILABLE) return
        dispatch(entityId, action, emptyMap())
    }

    /** 슬라이더를 끌 때마다 부르면 마지막 값만 [BRIGHTNESS_DEBOUNCE_MS] 뒤에 한 번 보낸다. */
    fun onBrightnessChange(entityId: String, pct: Int) {
        brightnessJobs.remove(entityId)?.cancel()
        brightnessJobs[entityId] = viewModelScope.launch {
            delay(BRIGHTNESS_DEBOUNCE_MS)
            brightnessJobs.remove(entityId)
            dispatch(entityId, ACTION_BRIGHTNESS, mapOf(PARAM_BRIGHTNESS_PCT to pct.coerceIn(0, 100).toString()))
        }
    }

    fun onErrorShown() {
        error.value = null
    }

    fun onScreenShown() = restartInactivityTimer()

    fun onScreenHidden() {
        inactivityJob?.cancel()
        inactivityJob = null
    }

    /** 터치가 있을 때마다 비활동 시간을 다시 센다. */
    fun onUserInteraction() {
        if (inactivityJob == null) return
        restartInactivityTimer()
    }

    private fun restartInactivityTimer() {
        inactivityJob?.cancel()
        inactivityJob = viewModelScope.launch {
            delay(INACTIVITY_TIMEOUT_MS)
            inactivityJob = null
            _returnToAmbient.trySend(Unit)
        }
    }

    private fun dispatch(entityId: String, action: String, params: Map<String, String>) {
        val baseline = home.state.value.entities[entityId]?.state
        pending.update { it + entityId }
        viewModelScope.launch {
            try {
                when (val outcome = sender.send(entityId, action, params)) {
                    is CommandOutcome.Failure -> {
                        Timber.w("Command %s on %s failed: %s", action, entityId, outcome.reason)
                        error.value = outcome.reason
                    }
                    CommandOutcome.Success -> awaitStateChange(entityId, baseline)
                }
            } finally {
                pending.update { it - entityId }
            }
        }
    }

    // 성공 응답 뒤에도 실제 상태는 EntityStateChanged 로 온다. 오지 않는 명령(이미 같은 상태)이 카드를 붙잡지 않게 시한을 둔다.
    private suspend fun awaitStateChange(entityId: String, baseline: Any?) {
        withTimeoutOrNull(STATE_WAIT_MS) {
            home.state.first { it.entities[entityId]?.state != baseline }
        }
    }

    companion object {
        const val INACTIVITY_TIMEOUT_MS = 60_000L
        const val BRIGHTNESS_DEBOUNCE_MS = 300L
        const val STATE_WAIT_MS = 3_000L
        private const val STOP_TIMEOUT_MS = 5_000L
        private const val ACTION_ON = "turn_on"
        private const val ACTION_OFF = "turn_off"
        private const val ACTION_ACTIVATE = "activate"
        private const val ACTION_RUN = "run"
        private const val ACTION_BRIGHTNESS = "set_brightness"
        private const val PARAM_BRIGHTNESS_PCT = "brightness_pct"
    }
}
