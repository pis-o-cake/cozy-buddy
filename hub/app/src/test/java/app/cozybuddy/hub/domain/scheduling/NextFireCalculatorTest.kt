package app.cozybuddy.hub.domain.scheduling

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import java.time.DayOfWeek
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test

class NextFireCalculatorTest {

    private val seoul = ZoneId.of("Asia/Seoul")

    // 2026-11-13 은 금요일이다.
    private fun at(day: Int, hour: Int, minute: Int, zone: ZoneId = seoul) =
        ZonedDateTime.of(2026, 11, day, hour, minute, 0, 0, zone)

    private fun repeating(hour: Int, minute: Int, vararg days: DayOfWeek, enabled: Boolean = true) = Alarm(
        id = "a",
        kind = AlarmKind.ALARM,
        label = "",
        schedule = AlarmSchedule.Repeating(hour, minute, days.toSet()),
        enabled = enabled,
    )

    @Test
    fun `later today is chosen when the time has not passed`() {
        val next = NextFireCalculator.nextFire(repeating(18, 0, DayOfWeek.FRIDAY), at(13, 9, 0))

        assertEquals(at(13, 18, 0).toInstant(), next)
    }

    @Test
    fun `same weekday moves a full week when today already passed`() {
        val next = NextFireCalculator.nextFire(repeating(8, 0, DayOfWeek.FRIDAY), at(13, 9, 0))

        assertEquals(at(20, 8, 0).toInstant(), next)
    }

    @Test
    fun `exact fire time is not reused`() {
        val next = NextFireCalculator.nextFire(repeating(9, 0, DayOfWeek.FRIDAY), at(13, 9, 0))

        assertEquals(at(20, 9, 0).toInstant(), next)
    }

    @Test
    fun `search crosses the week boundary from Sunday to Monday`() {
        // 2026-11-15 은 일요일. 월요일만 설정하면 다음 날(16일)이다.
        val next = NextFireCalculator.nextFire(repeating(7, 0, DayOfWeek.MONDAY), at(15, 22, 0))

        assertEquals(at(16, 7, 0).toInstant(), next)
    }

    @Test
    fun `picks the nearest of several days`() {
        val next = NextFireCalculator.nextFire(
            repeating(7, 0, DayOfWeek.MONDAY, DayOfWeek.WEDNESDAY, DayOfWeek.SATURDAY),
            at(13, 9, 0),
        )

        assertEquals(at(14, 7, 0).toInstant(), next)
    }

    @Test
    fun `empty days means every day`() {
        val next = NextFireCalculator.nextFire(repeating(7, 0), at(13, 9, 0))

        assertEquals(at(14, 7, 0).toInstant(), next)
    }

    @Test
    fun `wall time follows the hub zone`() {
        val utc = ZoneId.of("UTC")
        val next = NextFireCalculator.nextFire(repeating(7, 0), at(13, 0, 0, utc))

        assertEquals(at(13, 7, 0, utc).toInstant(), next)
    }

    @Test
    fun `daylight saving gap keeps the alarm on the wall clock day`() {
        // 2026-03-08 02:30 America/New_York 는 존재하지 않는다.
        val ny = ZoneId.of("America/New_York")
        val now = ZonedDateTime.of(2026, 3, 7, 23, 0, 0, 0, ny)

        val next = NextFireCalculator.nextFire(repeating(2, 30, DayOfWeek.SUNDAY), now)

        assertEquals(ZonedDateTime.of(2026, 3, 8, 3, 30, 0, 0, ny).toInstant(), next)
    }

    @Test
    fun `disabled alarm never fires`() {
        assertNull(NextFireCalculator.nextFire(repeating(7, 0, enabled = false), at(13, 9, 0)))
    }

    @Test
    fun `future one-shot returns its own time`() {
        val target = at(13, 12, 0).toInstant()
        val alarm = Alarm("t", AlarmKind.TIMER, "", AlarmSchedule.OneShot(target.toEpochMilli()), enabled = true)

        assertEquals(target, NextFireCalculator.nextFire(alarm, at(13, 9, 0)))
    }

    @Test
    fun `past one-shot is skipped`() {
        val past = at(13, 8, 0).toInstant()
        val alarm = Alarm("t", AlarmKind.ALARM, "", AlarmSchedule.OneShot(past.toEpochMilli()), enabled = true)

        assertNull(NextFireCalculator.nextFire(alarm, at(13, 9, 0)))
        assertNull(NextFireCalculator.nextFire(alarm.copy(schedule = AlarmSchedule.OneShot(Instant.EPOCH.toEpochMilli())), at(13, 9, 0)))
    }
}
