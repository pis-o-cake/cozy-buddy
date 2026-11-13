package app.cozybuddy.hub.core.audio

/** 프레임 단위 음성 확률 추정기. 상태를 가지므로 발화마다 [reset] 한다. */
interface Vad {
    /** [CaptureFormat.FRAME_SAMPLES] 샘플 프레임 하나의 음성 확률(0..1). */
    suspend fun speechProbability(frame: ShortArray): Float

    fun reset()
}

/** 발화 종료 판단 파라미터. 문턱값은 실기기 마이크로 조정해야 한다. */
data class VadConfig(
    val speechThreshold: Float = 0.5f,
    /** 발화 중 이 값 아래면 무음으로 센다. 문턱보다 낮게 둬 경계에서 떨리지 않게 한다. */
    val silenceThreshold: Float = 0.35f,
    /** 연속으로 이만큼 음성이어야 발화 시작으로 인정한다(잡음 한 프레임 무시). */
    val minSpeechFrames: Int = 2,
    val endSilenceMs: Int = 700,
    val maxUtteranceMs: Int = 15_000,
    val noSpeechTimeoutMs: Int = 8_000,
)

enum class VadDecision { CONTINUE, END_OF_SPEECH, NO_SPEECH, MAX_LENGTH }

/**
 * 프레임별 확률 열을 받아 발화 종료·무발화·최대 길이를 판단하는 순수 로직.
 *
 * 시간은 벽시계가 아니라 처리한 프레임 수로 센다. 캡처가 프레임을 지연해도 판단이 흔들리지 않고 테스트가 결정적이다.
 */
class EndOfSpeechDetector(
    private val config: VadConfig = VadConfig(),
    private val frameMs: Int = CaptureFormat.FRAME_MS,
) {
    private var elapsedMs = 0
    private var speechRun = 0
    private var speechStarted = false
    private var silenceMs = 0

    val hasSpeech: Boolean get() = speechStarted

    fun onFrame(probability: Float): VadDecision {
        elapsedMs += frameMs
        if (speechStarted) {
            if (probability < config.silenceThreshold) silenceMs += frameMs else silenceMs = 0
            if (silenceMs >= config.endSilenceMs) return VadDecision.END_OF_SPEECH
        } else {
            speechRun = if (probability >= config.speechThreshold) speechRun + 1 else 0
            if (speechRun >= config.minSpeechFrames) speechStarted = true
        }
        if (elapsedMs >= config.maxUtteranceMs) return VadDecision.MAX_LENGTH
        if (!speechStarted && elapsedMs >= config.noSpeechTimeoutMs) return VadDecision.NO_SPEECH
        return VadDecision.CONTINUE
    }
}
