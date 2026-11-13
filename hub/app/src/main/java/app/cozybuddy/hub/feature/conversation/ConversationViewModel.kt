package app.cozybuddy.hub.feature.conversation

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.domain.voice.AssistantPhase
import app.cozybuddy.hub.domain.voice.VoiceClient
import app.cozybuddy.hub.domain.voice.VoiceNotice
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

object ConversationConfig {
    /** 대화가 IDLE 로 끝난 뒤 오버레이가 남아 있는 시간. */
    const val AUTO_DISMISS_MS = 4_000L

    /** 파형에 그리는 최근 레벨 개수. */
    const val LEVEL_HISTORY = 28
}

enum class ConversationNotice { NONE, OFFLINE, MIC_UNAVAILABLE, ERROR }

data class ConversationUiState(
    val visible: Boolean = false,
    val phase: AssistantPhase = AssistantPhase.IDLE,
    /** 듣는 중 최근 마이크 레벨(0..1), 오래된 것부터. */
    val levels: List<Float> = emptyList(),
    val transcript: String = "",
    val response: String = "",
    val notice: ConversationNotice = ConversationNotice.NONE,
    /** 알람이 울리는 동안은 대화를 시작할 수 없으므로 버튼을 감춘다. */
    val micEnabled: Boolean = true,
)

@HiltViewModel
class ConversationViewModel @Inject constructor(
    private val client: VoiceClient,
) : ViewModel() {

    private val visible = MutableStateFlow(false)
    private val notice = MutableStateFlow(ConversationNotice.NONE)
    private val levels = MutableStateFlow<List<Float>>(emptyList())

    val uiState: StateFlow<ConversationUiState> = combine(
        client.state,
        client.conversation,
        visible,
        notice,
        levels,
    ) { snapshot, text, isVisible, currentNotice, history ->
        ConversationUiState(
            visible = isVisible,
            phase = snapshot.phase,
            levels = history,
            transcript = text.transcript,
            response = text.response,
            notice = currentNotice,
            micEnabled = !snapshot.alarmRinging,
        )
    }.stateIn(viewModelScope, SharingStarted.Eagerly, ConversationUiState())

    init {
        viewModelScope.launch {
            client.notices.collect { value ->
                notice.value = value.toNotice()
                visible.value = true
            }
        }
        viewModelScope.launch {
            // 알림(notice)도 같은 4초 규칙으로 사라지므로 두 흐름을 함께 보고, 바뀔 때마다 타이머를 다시 건다.
            combine(client.state, notice, visible) { snapshot, current, shown -> Triple(snapshot.phase, current, shown) }
                .collectLatest { (phase, current, shown) -> syncVisibility(phase, current, shown) }
        }
        viewModelScope.launch {
            combine(client.micLevel, client.state) { level, snapshot -> level to snapshot.phase }
                .collect { (level, phase) -> recordLevel(level, phase) }
        }
    }

    /** 마이크 버튼. */
    fun onMicTap() = client.onTap()

    /** 오버레이 본문 탭. 응답 중이면 끼어들기, 남아 있는 중이면 즉시 닫는다. */
    fun onOverlayTap() {
        if (uiState.value.phase == AssistantPhase.SPEAKING) {
            client.onTap()
            return
        }
        if (uiState.value.phase == AssistantPhase.IDLE) dismiss()
    }

    private fun dismiss() {
        visible.value = false
        notice.value = ConversationNotice.NONE
    }

    private suspend fun syncVisibility(phase: AssistantPhase, current: ConversationNotice, shown: Boolean) {
        if (phase != AssistantPhase.IDLE) {
            visible.value = true
            // 새 대화가 시작되면 이전 알림은 지운다.
            if (current != ConversationNotice.NONE) notice.value = ConversationNotice.NONE
            return
        }
        if (!shown) return
        delay(ConversationConfig.AUTO_DISMISS_MS)
        dismiss()
    }

    private fun recordLevel(level: Float, phase: AssistantPhase) {
        if (phase != AssistantPhase.LISTENING) {
            levels.update { if (it.isEmpty()) it else emptyList() }
            return
        }
        levels.update { (it + level).takeLast(ConversationConfig.LEVEL_HISTORY) }
    }

    private fun VoiceNotice.toNotice(): ConversationNotice = when (this) {
        VoiceNotice.OFFLINE -> ConversationNotice.OFFLINE
        VoiceNotice.MIC_UNAVAILABLE -> ConversationNotice.MIC_UNAVAILABLE
        VoiceNotice.ERROR -> ConversationNotice.ERROR
    }
}
