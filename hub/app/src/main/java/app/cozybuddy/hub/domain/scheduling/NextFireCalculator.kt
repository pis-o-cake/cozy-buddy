package app.cozybuddy.hub.domain.scheduling

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmSchedule
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalTime
import java.time.ZonedDateTime

/**
 * 다음 울림 시각을 구하는 순수 함수 모음.
 *
 * 반복 알람은 [now] 의 시간대 벽시계로 계산하므로 DST 전환일에도 "그 시각" 을 유지한다.
 */
object NextFireCalculator {

    private const val DAYS_IN_WEEK = 7

    /**
     * [now] 이후(엄격히 미래)의 가장 가까운 울림 시각.
     *
     * @return 비활성이거나 더 울릴 일이 없으면(과거의 일회성) null.
     */
    fun nextFire(alarm: Alarm, now: ZonedDateTime): Instant? {
        if (!alarm.enabled) return null
        return when (val schedule = alarm.schedule) {
            is AlarmSchedule.OneShot -> Instant.ofEpochMilli(schedule.firesAtEpochMs).takeIf { it.isAfter(now.toInstant()) }
            is AlarmSchedule.Repeating -> nextRepeating(schedule, now)
        }
    }

    private fun nextRepeating(schedule: AlarmSchedule.Repeating, now: ZonedDateTime): Instant? {
        val days = schedule.days.ifEmpty { DayOfWeek.entries.toSet() }
        val time = LocalTime.of(schedule.hour, schedule.minute)
        // 오늘 포함 8일을 보면 같은 요일 하나만 골라도 반드시 미래 후보가 나온다.
        for (offset in 0..DAYS_IN_WEEK) {
            val date = now.toLocalDate().plusDays(offset.toLong())
            if (date.dayOfWeek !in days) continue
            // DST 로 존재하지 않는 시각은 ZonedDateTime 이 앞으로 밀어 준다.
            val candidate = ZonedDateTime.of(date, time, now.zone).toInstant()
            if (candidate.isAfter(now.toInstant())) return candidate
        }
        return null
    }
}
