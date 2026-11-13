package app.cozybuddy.hub.feature.alarm

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class AlarmRingConfigTest {

    @Test
    fun `volume starts low and reaches full at the end of the ramp`() {
        assertEquals(AlarmRingConfig.START_VOLUME, AlarmRingConfig.volumeAt(0))
        assertEquals(1f, AlarmRingConfig.volumeAt(30_000))
        assertEquals(1f, AlarmRingConfig.volumeAt(120_000))
    }

    @Test
    fun `volume rises monotonically inside the ramp`() {
        val samples = (0..30_000 step 1_500).map { AlarmRingConfig.volumeAt(it.toLong()) }

        assertTrue(samples.zipWithNext().all { (a, b) -> b >= a })
        assertEquals(0.55f, AlarmRingConfig.volumeAt(15_000), 0.001f)
    }

    @Test
    fun `auto stop is ten minutes`() {
        assertEquals(600_000L, AlarmRingConfig.AUTO_STOP.toMillis())
    }
}
