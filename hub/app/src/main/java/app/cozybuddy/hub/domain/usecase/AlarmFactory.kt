package app.cozybuddy.hub.domain.usecase

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.hub.domain.scheduling.NextFireCalculator
import java.time.DayOfWeek
import java.time.ZonedDateTime

/** 새 알람·타이머 생성 규칙. 음성·서버 생성 경로가 생기기 전까지는 목록 화면의 개발용 추가가 쓴다. */
object AlarmFactory {

    /** [days] 가 비면 [now] 이후 가장 가까운 해당 시각에 한 번만 울리는 알람이다. */
    fun newAlarm(id: String, hour: Int, minute: Int, days: Set<DayOfWeek>, now: ZonedDateTime): Alarm {
        if (days.isNotEmpty()) {
            return Alarm(id, AlarmKind.ALARM, "", AlarmSchedule.Repeating(hour, minute, days), enabled = true)
        }
        val daily = Alarm(id, AlarmKind.ALARM, "", AlarmSchedule.Repeating(hour, minute, emptySet()), enabled = true)
        val fireAt = checkNotNull(NextFireCalculator.nextFire(daily, now)) { "daily alarm must have a next fire" }
        return daily.copy(schedule = AlarmSchedule.OneShot(fireAt.toEpochMilli()))
    }

    fun newTimer(id: String, minutes: Long, now: ZonedDateTime): Alarm = Alarm(
        id = id,
        kind = AlarmKind.TIMER,
        label = "",
        schedule = AlarmSchedule.OneShot(now.plusMinutes(minutes).toInstant().toEpochMilli()),
        enabled = true,
    )
}
