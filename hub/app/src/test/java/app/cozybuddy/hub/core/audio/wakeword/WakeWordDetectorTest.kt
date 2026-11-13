package app.cozybuddy.hub.core.audio.wakeword

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class WakeWordDetectorTest {

    private fun detector(hits: Int = 3, refractoryMs: Int = 240) =
        WakeWordDetector(consecutiveHits = hits, refractoryMs = refractoryMs, threshold = 0.5f)

    @Test
    fun `fires only after the required number of consecutive hits`() {
        val d = detector()

        assertFalse(d.onScore(0.9f))
        assertFalse(d.onScore(0.9f))
        assertTrue(d.onScore(0.9f))
    }

    @Test
    fun `a miss resets the run`() {
        val d = detector()

        d.onScore(0.9f)
        d.onScore(0.9f)
        assertFalse(d.onScore(0.1f))
        assertFalse(d.onScore(0.9f))
        assertFalse(d.onScore(0.9f))
        assertTrue(d.onScore(0.9f))
    }

    @Test
    fun `score equal to the threshold counts as a hit`() {
        val d = detector(hits = 1)

        assertTrue(d.onScore(0.5f))
    }

    @Test
    fun `refractory period suppresses repeats then releases`() {
        val d = detector(hits = 1, refractoryMs = 240)

        assertTrue(d.onScore(0.9f))
        // 불응 240 ms = 예측 3 개.
        assertFalse(d.onScore(0.9f))
        assertFalse(d.onScore(0.9f))
        assertFalse(d.onScore(0.9f))
        assertTrue(d.onScore(0.9f))
    }

    @Test
    fun `hits during the refractory period do not count toward the next detection`() {
        val d = detector(hits = 2, refractoryMs = 160)
        d.onScore(0.9f)
        assertTrue(d.onScore(0.9f))

        assertFalse(d.onScore(0.9f))
        assertFalse(d.onScore(0.9f))
        assertFalse(d.onScore(0.9f))
        assertTrue(d.onScore(0.9f))
    }

    @Test
    fun `threshold change applies to the next score`() {
        val d = detector(hits = 1)
        assertFalse(d.onScore(0.4f))

        d.threshold = 0.3f

        assertTrue(d.onScore(0.4f))
    }

    @Test
    fun `reset clears run and refractory`() {
        val d = detector(hits = 1)
        assertTrue(d.onScore(0.9f))

        d.reset()

        assertTrue(d.onScore(0.9f))
    }

    @Test
    fun `consecutive hits below one is rejected`() {
        assertThrows(IllegalArgumentException::class.java) { detector(hits = 0) }
    }

    @Test
    fun `sensitivity maps monotonically to a lower threshold`() {
        assertEquals(0.8f, WakeSensitivity.toThreshold(0f), 1e-6f)
        assertEquals(0.5f, WakeSensitivity.toThreshold(WakeSensitivity.DEFAULT), 1e-6f)
        assertEquals(0.3f, WakeSensitivity.toThreshold(1f), 1e-6f)
        assertEquals(0.3f, WakeSensitivity.toThreshold(5f), 1e-6f)
    }
}
