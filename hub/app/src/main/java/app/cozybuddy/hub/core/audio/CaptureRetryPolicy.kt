package app.cozybuddy.hub.core.audio

/** 마이크 재시작 재시도 간격. [initialMs] 에서 시작해 두 배씩 늘리고 [maxMs] 에서 멈춘다. */
class CaptureRetryPolicy(
    private val initialMs: Long = INITIAL_MS,
    private val maxMs: Long = MAX_MS,
) {
    private var next = initialMs

    /** 다음 재시도까지 기다릴 시간(ms). 호출할 때마다 간격이 늘어난다. */
    fun nextDelayMs(): Long {
        val current = next
        next = minOf(next * 2, maxMs)
        return current
    }

    fun reset() {
        next = initialMs
    }

    companion object {
        const val INITIAL_MS = 1_000L
        const val MAX_MS = 30_000L
    }
}
