package app.cozybuddy.hub.core.network

import kotlin.random.Random

/** 재연결 지연: 1초에서 시작해 두 배씩 늘리고 [maxMs] 에서 멈춘 뒤 지터를 더한다. */
class BackoffPolicy(
    private val baseMs: Long = 1_000L,
    private val maxMs: Long = 30_000L,
    private val jitterRatio: Double = 0.2,
    private val random: Random = Random.Default,
) {
    /** [attempt] 는 0부터. 지터는 동시에 끊긴 허브들이 한꺼번에 재접속하지 않게 하려는 것이다. */
    fun delayMs(attempt: Int): Long {
        val exponent = attempt.coerceIn(0, MAX_EXPONENT)
        val capped = minOf(maxMs, baseMs shl exponent)
        return capped + (capped * jitterRatio * random.nextDouble()).toLong()
    }

    private companion object {
        const val MAX_EXPONENT = 20
    }
}
