package app.cozybuddy.hub.domain.voice

import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

enum class AssistantPhase { IDLE, LISTENING, THINKING, SPEAKING }

/**
 * 어시스턴트 상태의 단일 정본.
 *
 * @property utteranceId 현재 발화 스트림. IDLE 이면 null.
 * @property responseId 응답 오디오 스트림. SPEAKING 일 때만 값이 있다.
 * @property alarmRinging 알람이 울리는 동안 true. 대화를 시작할 수 없다.
 */
data class AssistantSnapshot(
    val phase: AssistantPhase = AssistantPhase.IDLE,
    val utteranceId: String? = null,
    val responseId: String? = null,
    val alarmRinging: Boolean = false,
)

enum class TapIgnoredReason { ALARM_RINGING, BUSY }

sealed interface TapResult {
    /** IDLE 에서 새 발화를 시작했다. */
    data object Started : TapResult

    /** SPEAKING 중 탭. [supersededUtteranceId] 의 응답을 끊고 새 발화를 시작했다. */
    data class BargeIn(val supersededUtteranceId: String) : TapResult

    data class Ignored(val reason: TapIgnoredReason) : TapResult
}

/**
 * IDLE → LISTENING → THINKING → SPEAKING → IDLE 전이만 담당하는 순수 상태 기계.
 *
 * 부수 효과(전송, 재생, 타이머)는 하지 않는다. 각 메서드는 전이가 일어났는지 돌려주고, 오케스트레이터가 그 결과에 따라 동작한다.
 * 서버가 보내는 `AssistantState` 는 여기에 반영하지 않는다. UI 의 정본은 항상 이 기계다.
 */
@Singleton
class AssistantStateMachine @Inject constructor() {

    private val _state = MutableStateFlow(AssistantSnapshot())
    val state: StateFlow<AssistantSnapshot> = _state.asStateFlow()

    fun onTap(newUtteranceId: String): TapResult {
        var result: TapResult = TapResult.Ignored(TapIgnoredReason.BUSY)
        _state.update { current ->
            result = when {
                current.alarmRinging -> TapResult.Ignored(TapIgnoredReason.ALARM_RINGING)
                current.phase == AssistantPhase.IDLE -> TapResult.Started
                current.phase == AssistantPhase.SPEAKING ->
                    TapResult.BargeIn(requireNotNull(current.utteranceId) { "SPEAKING without utterance" })
                else -> TapResult.Ignored(TapIgnoredReason.BUSY)
            }
            if (result is TapResult.Ignored) current else listening(current, newUtteranceId)
        }
        return result
    }

    /**
     * 호출어 감지. 탭과 달리 알람이 울리는 중에도 IDLE 이면 듣기를 시작한다("자비스, 알람 꺼").
     * 알람을 실제로 끌지는 서버 응답 경로가 정한다.
     */
    fun onWakeWord(newUtteranceId: String): TapResult {
        var result: TapResult = TapResult.Ignored(TapIgnoredReason.BUSY)
        _state.update { current ->
            result = when (current.phase) {
                AssistantPhase.IDLE -> TapResult.Started
                AssistantPhase.SPEAKING ->
                    TapResult.BargeIn(requireNotNull(current.utteranceId) { "SPEAKING without utterance" })
                else -> TapResult.Ignored(TapIgnoredReason.BUSY)
            }
            if (result is TapResult.Ignored) current else listening(current, newUtteranceId)
        }
        return result
    }

    /** VAD 가 발화 끝을 판단해 UtteranceEnd 를 보냈다. */
    fun onUtteranceEnded(utteranceId: String): Boolean =
        transition(AssistantPhase.LISTENING, utteranceId) { it.copy(phase = AssistantPhase.THINKING) }

    /** 무발화 시간 초과. */
    fun onNoSpeech(utteranceId: String): Boolean =
        transition(AssistantPhase.LISTENING, utteranceId) { idle(it) }

    fun onResponseAudioStart(responseId: String): Boolean =
        transition(AssistantPhase.THINKING, null) {
            it.copy(phase = AssistantPhase.SPEAKING, responseId = responseId)
        }

    /** 응답 스트림이 끝났고 재생도 모두 소진됐다. */
    fun onPlaybackDrained(responseId: String): Boolean =
        transition(AssistantPhase.SPEAKING, null, responseId) { idle(it) }

    /** 오류나 연결 끊김. 어느 상태에서든 IDLE 로 돌아간다. 이미 IDLE 이면 false. */
    fun onError(): Boolean = reset()

    fun onDisconnected(): Boolean = reset()

    /** 알람이 울리기 시작하면 진행 중인 대화를 끊고 잠근다. 멈추면 잠금만 푼다. */
    fun onAlarmRinging(ringing: Boolean) {
        _state.update { current ->
            if (ringing) idle(current).copy(alarmRinging = true) else current.copy(alarmRinging = false)
        }
    }

    private fun reset(): Boolean {
        var changed = false
        _state.update { current ->
            changed = current.phase != AssistantPhase.IDLE
            idle(current)
        }
        return changed
    }

    private fun transition(
        from: AssistantPhase,
        utteranceId: String?,
        responseId: String? = null,
        next: (AssistantSnapshot) -> AssistantSnapshot,
    ): Boolean {
        var changed = false
        _state.update { current ->
            val matches = current.phase == from &&
                (utteranceId == null || current.utteranceId == utteranceId) &&
                (responseId == null || current.responseId == responseId)
            changed = matches
            if (matches) next(current) else current
        }
        return changed
    }

    private fun listening(current: AssistantSnapshot, utteranceId: String) = current.copy(
        phase = AssistantPhase.LISTENING,
        utteranceId = utteranceId,
        responseId = null,
    )

    private fun idle(current: AssistantSnapshot) = current.copy(
        phase = AssistantPhase.IDLE,
        utteranceId = null,
        responseId = null,
    )
}
