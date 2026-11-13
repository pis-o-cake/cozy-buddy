package app.cozybuddy.hub.core.audio.wakeword

/**
 * 예측 점수 열에서 감지를 확정하는 순수 로직: 문턱 이상이 N 번 연속 + 불응 구간.
 *
 * 시간은 벽시계가 아니라 처리한 예측 수로 센다. 입력이 지연돼도 판단이 흔들리지 않고 테스트가 결정적이다.
 */
class WakeWordDetector(
    private val consecutiveHits: Int,
    private val refractoryMs: Int,
    threshold: Float,
    private val predictionMs: Int = OpenWakeWordFormat.CHUNK_MS,
) {
    @Volatile
    var threshold: Float = threshold

    private var run = 0
    private var refractoryLeftMs = 0

    init {
        require(consecutiveHits >= 1) { "consecutiveHits must be >= 1" }
    }

    /** 예측 하나를 소비한다. 감지가 확정되면 true. */
    fun onScore(score: Float): Boolean {
        if (refractoryLeftMs > 0) {
            refractoryLeftMs = (refractoryLeftMs - predictionMs).coerceAtLeast(0)
            run = 0
            return false
        }
        run = if (score >= threshold) run + 1 else 0
        if (run < consecutiveHits) return false
        run = 0
        refractoryLeftMs = refractoryMs
        return true
    }

    fun reset() {
        run = 0
        refractoryLeftMs = 0
    }
}
