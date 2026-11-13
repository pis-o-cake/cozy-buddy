package app.cozybuddy.hub.core.audio.wakeword

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

/**
 * 호출어가 감지됐다.
 *
 * @property score 감지를 확정한 시점의 분류기 점수(0..1).
 * @property streamPositionMs 엔진이 처리한 누적 오디오 길이. 로그와 테스트에서 시각을 비교하는 용도다.
 */
data class WakeEvent(val score: Float, val streamPositionMs: Long)

/** 엔진을 쓸 수 없는 이유. 문구는 화면이 리소스에서 해석한다. */
enum class WakeWordFailure { MODEL_MISSING, MODEL_CORRUPT, RUNTIME }

sealed interface WakeWordStatus {
    /** 아직 초기화하지 않았다. */
    data object Idle : WakeWordStatus

    data object Ready : WakeWordStatus

    data class Failed(val failure: WakeWordFailure) : WakeWordStatus
}

/**
 * 온디바이스 호출어 엔진.
 *
 * IMPORTANT: 마이크 프레임은 기기 밖으로 나가지 않는다. 감지 이전의 오디오는 서버로 보내지 않는다.
 */
interface WakeWordEngine {
    val status: StateFlow<WakeWordStatus>

    /** 감지 사건. 불응 구간 안의 반복 감지는 내보내지 않는다. */
    val events: Flow<WakeEvent>

    /** 모델을 읽고 무결성을 검증한다. 실패해도 예외 대신 [status] 가 [WakeWordStatus.Failed] 가 된다. 준비됐으면 true. */
    suspend fun initialize(): Boolean

    /**
     * 캡처 프레임([app.cozybuddy.hub.core.audio.CaptureFormat.FRAME_SAMPLES] 샘플)을 넣고 가장 최근 점수를 돌려준다.
     * 새 예측이 만들어지지 않은 프레임은 직전 점수를 그대로 돌려준다. 준비되지 않았으면 0.
     */
    suspend fun process(frame: ShortArray): Float

    /** 감지 문턱(0..1). 다음 예측부터 적용된다. */
    fun setThreshold(threshold: Float)

    /** 스트림이 끊겼다 이어질 때 내부 오디오 상태를 비운다. 불응 구간도 함께 푼다. */
    fun reset()

    fun release()
}
