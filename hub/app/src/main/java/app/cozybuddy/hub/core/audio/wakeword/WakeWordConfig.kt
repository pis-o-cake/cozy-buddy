package app.cozybuddy.hub.core.audio.wakeword

/** openWakeWord 파이프라인이 모델과 맞춰 둔 고정 상수. 값을 바꾸면 모델 입력이 어긋난다. */
object OpenWakeWordFormat {
    /** 예측 한 번에 쓰는 샘플 수(80 ms @ 16 kHz). */
    const val CHUNK_SAMPLES = 1_280

    /** 청크 앞에 붙이는 이전 오디오. 멜 프레임 경계를 청크 사이에서 이어 준다(160 샘플 hop 의 3 배). */
    const val MEL_CONTEXT_SAMPLES = 480
    const val MEL_BINS = 32
    const val EMBEDDING_WINDOW_FRAMES = 76
    const val EMBEDDING_DIM = 96
    const val CLASSIFIER_FRAMES = 16

    /** 버퍼가 차기 전의 예측은 점수를 0 으로 눌러 초기 오탐을 막는다. 참조 구현과 같은 값이다. */
    const val WARMUP_PREDICTIONS = 5
    const val CHUNK_MS = 80
}

/** 어시스턴트가 말하는 동안 호출어를 어떻게 다룰지. */
enum class WakeSpeakingPolicy { GATE_OFF, RAISED_THRESHOLD }

/**
 * 감지 파라미터.
 *
 * @property consecutiveHits 문턱 이상이 이만큼 연속돼야 감지로 확정한다(청크 하나 = 80 ms).
 * @property refractoryMs 감지 직후 이 시간 동안은 재감지하지 않는다.
 * @property speakingPolicy 어시스턴트가 말하는 중의 동작. 기본은 문턱을 올려 끼어들기만 허용한다.
 * @property speakingThresholdBoost [WakeSpeakingPolicy.RAISED_THRESHOLD] 일 때 문턱에 더하는 값.
 */
data class WakeWordConfig(
    val consecutiveHits: Int = 2,
    val refractoryMs: Int = 2_000,
    val speakingPolicy: WakeSpeakingPolicy = WakeSpeakingPolicy.RAISED_THRESHOLD,
    val speakingThresholdBoost: Float = 0.25f,
)

/** 사용자 감도(0..1)와 분류기 문턱 사이의 변환. 감도가 높을수록 문턱이 낮다. */
object WakeSensitivity {
    const val MIN_THRESHOLD = 0.3f
    const val MAX_THRESHOLD = 0.8f

    /** 오프라인 평가(spikes/s1-wakeword)로 고른 기본 문턱에 대응하는 감도. */
    const val DEFAULT = 0.6f

    /** 문턱이 이 값을 넘으면 점수가 닿지 못하므로 상한을 둔다. */
    const val THRESHOLD_CEILING = 0.97f

    fun toThreshold(sensitivity: Float): Float {
        val s = sensitivity.coerceIn(0f, 1f)
        return MAX_THRESHOLD - (MAX_THRESHOLD - MIN_THRESHOLD) * s
    }
}
