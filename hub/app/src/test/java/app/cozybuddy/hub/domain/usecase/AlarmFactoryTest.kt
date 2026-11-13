package app.cozybuddy.hub.domain.usecase

import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import java.time.DayOfWeek
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class AlarmFactoryTest {

    private val now = ZonedDateTime.of(2026, 11, 13, 9, 0, 0, 0, ZoneId.of("Asia/Seoul"))

    @Test
    fun `alarm with days is repeating`() {
        val alarm = AlarmFactory.newAlarm("a", 7, 0, setOf(DayOfWeek.MONDAY), now)

        assertEquals(AlarmSchedule.Repeating(7, 0, setOf(DayOfWeek.MONDAY)), alarm.schedule)
    }

    @Test
    fun `alarm without days rings once at the next occurrence`() {
        val later = AlarmFactory.newAlarm("a", 18, 0, emptySet(), now)
        val tomorrow = AlarmFactory.newAlarm("a", 8, 0, emptySet(), now)

        assertEquals(AlarmSchedule.OneShot(now.withHour(18).toInstant().toEpochMilli()), later.schedule)
        assertEquals(AlarmSchedule.OneShot(now.plusDays(1).withHour(8).toInstant().toEpochMilli()), tomorrow.schedule)
    }

    @Test
    fun `timer fires after the given minutes`() {
        val timer = AlarmFactory.newTimer("t", 1, now)

        assertEquals(AlarmKind.TIMER, timer.kind)
        assertEquals(AlarmSchedule.OneShot(now.plusMinutes(1).toInstant().toEpochMilli()), timer.schedule)
    }
}
