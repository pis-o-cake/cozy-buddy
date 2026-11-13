package app.cozybuddy.hub.data.protocol

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.protocol.v1.alarm
import app.cozybuddy.protocol.v1.oneShot
import app.cozybuddy.protocol.v1.repeating
import java.time.DayOfWeek
import app.cozybuddy.protocol.v1.Alarm as AlarmProto
import app.cozybuddy.protocol.v1.AlarmKind as AlarmKindProto
import app.cozybuddy.protocol.v1.DayOfWeek as DayOfWeekProto

/** 도메인 [Alarm] 을 전송용 protobuf 로 바꾼다. T6 동기화가 사용한다. */
fun Alarm.toProto(hubId: String): AlarmProto {
    val source = this
    return alarm {
        alarmId = source.id
        kind = source.kind.toProto()
        label = source.label
        enabled = source.enabled
        sunrise = source.sunrise
        revision = source.revision
        this.hubId = hubId
        when (val schedule = source.schedule) {
            is AlarmSchedule.OneShot -> oneShot = oneShot { firesAtEpochMs = schedule.firesAtEpochMs }
            is AlarmSchedule.Repeating -> repeating = repeating {
                hour = schedule.hour
                minute = schedule.minute
                days.addAll(schedule.days.sorted().map { it.toProto() })
            }
        }
    }
}

/**
 * protobuf 를 도메인으로 바꾼다.
 *
 * protobuf 에는 updated_at 이 없으므로 수신 시각을 [updatedAtEpochMs] 로 받는다.
 *
 * @throws IllegalArgumentException 종류·일정·요일이 지정되지 않았을 때.
 */
fun AlarmProto.toDomain(updatedAtEpochMs: Long): Alarm = Alarm(
    id = alarmId,
    kind = kind.toDomain(),
    label = label,
    schedule = when (scheduleCase) {
        AlarmProto.ScheduleCase.ONE_SHOT -> AlarmSchedule.OneShot(oneShot.firesAtEpochMs)
        AlarmProto.ScheduleCase.REPEATING -> AlarmSchedule.Repeating(
            hour = repeating.hour,
            minute = repeating.minute,
            days = repeating.daysList.mapTo(linkedSetOf()) { it.toDomain() },
        )
        else -> throw IllegalArgumentException("Alarm schedule is not set: $alarmId")
    },
    enabled = enabled,
    sunrise = sunrise,
    revision = revision,
    updatedAtEpochMs = updatedAtEpochMs,
)

private fun AlarmKind.toProto(): AlarmKindProto = when (this) {
    AlarmKind.ALARM -> AlarmKindProto.ALARM_KIND_ALARM
    AlarmKind.TIMER -> AlarmKindProto.ALARM_KIND_TIMER
}

private fun AlarmKindProto.toDomain(): AlarmKind = when (this) {
    AlarmKindProto.ALARM_KIND_ALARM -> AlarmKind.ALARM
    AlarmKindProto.ALARM_KIND_TIMER -> AlarmKind.TIMER
    else -> throw IllegalArgumentException("Unsupported alarm kind: $this")
}

// proto 번호(월=1..일=7)가 ISO-8601 과 같아 숫자로 직접 대응한다.
private fun DayOfWeek.toProto(): DayOfWeekProto = DayOfWeekProto.forNumber(value)

private fun DayOfWeekProto.toDomain(): DayOfWeek {
    require(this != DayOfWeekProto.DAY_OF_WEEK_UNSPECIFIED && this != DayOfWeekProto.UNRECOGNIZED) {
        "Unsupported day of week: $this"
    }
    return DayOfWeek.of(number)
}
