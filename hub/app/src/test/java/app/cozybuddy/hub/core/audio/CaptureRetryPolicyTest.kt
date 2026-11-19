package app.cozybuddy.hub.core.audio

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class CaptureRetryPolicyTest {

    @Test
    fun `delay doubles from one second and caps at thirty seconds`() {
        val policy = CaptureRetryPolicy()

        val delays = List(8) { policy.nextDelayMs() }

        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L, 30_000L), delays)
    }

    @Test
    fun `reset restarts from the initial delay`() {
        val policy = CaptureRetryPolicy()
        repeat(3) { policy.nextDelayMs() }

        policy.reset()

        assertEquals(1_000L, policy.nextDelayMs())
    }
}
