package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import java.time.DayOfWeek
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class AlarmUiModelsTest {

    private val zone = ZoneId.of("Asia/Seoul")
    private val locale = Locale.KOREA

    private fun repeating(days: Set<DayOfWeek>) =
        Alarm("a", AlarmKind.ALARM, "출근", AlarmSchedule.Repeating(7, 5, days), enabled = true)

    @Test
    fun `repeating alarm shows 24 hour time and weekdays in order`() {
        val item = repeating(setOf(DayOfWeek.FRIDAY, DayOfWeek.MONDAY)).toItemUi(zone, true, locale)

        assertEquals("07:05", item.timeText)
        assertEquals(AlarmDetail.Days("월 금"), item.detail)
    }

    @Test
    fun `empty or full day set means every day`() {
        assertEquals(AlarmDetail.EveryDay, repeating(emptySet()).toItemUi(zone, true, locale).detail)
        assertEquals(AlarmDetail.EveryDay, repeating(DayOfWeek.entries.toSet()).toItemUi(zone, true, locale).detail)
    }

    @Test
    fun `one-shot shows its local time in the hub zone`() {
        val at = ZonedDateTime.of(2026, 11, 13, 18, 30, 0, 0, zone).toInstant().toEpochMilli()
        val timer = Alarm("t", AlarmKind.TIMER, "", AlarmSchedule.OneShot(at), enabled = true)

        val item = timer.toItemUi(zone, true, locale)

        assertEquals("18:30", item.timeText)
        assertEquals(true, item.detail is AlarmDetail.Date)
    }
}
