package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.domain.model.AlarmKind
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
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
    fun `reminder uses a short non looping chime instead of the alarm loop`() {
        val reminder = AlarmRingConfig.profileFor(AlarmKind.REMINDER)
        val alarm = AlarmRingConfig.profileFor(AlarmKind.ALARM)

        assertFalse(reminder.looping)
        assertFalse(reminder.ramp)
        assertTrue(reminder.announcesLabel)
        assertEquals(AlarmRingConfig.REMINDER_CHIME_REPEATS, reminder.chimeRepeats)
        assertTrue(reminder.autoStop < alarm.autoStop)

        assertTrue(alarm.looping)
        assertTrue(alarm.ramp)
        assertFalse(alarm.announcesLabel)
        assertEquals(AlarmRingConfig.AUTO_STOP, alarm.autoStop)
    }

    @Test
    fun `timer rings like an alarm`() {
        assertEquals(AlarmRingConfig.profileFor(AlarmKind.ALARM), AlarmRingConfig.profileFor(AlarmKind.TIMER))
    }

    @Test
    fun `auto stop is ten minutes`() {
        assertEquals(600_000L, AlarmRingConfig.AUTO_STOP.toMillis())
    }
}
