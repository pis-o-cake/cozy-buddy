package app.cozybuddy.hub.data.local

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import java.time.DayOfWeek

internal fun Alarm.toEntity(): AlarmEntity {
    val oneShot = schedule as? AlarmSchedule.OneShot
    val repeating = schedule as? AlarmSchedule.Repeating
    return AlarmEntity(
        alarmId = id,
        kind = kind.name,
        label = label,
        firesAtEpochMs = oneShot?.firesAtEpochMs,
        hour = repeating?.hour,
        minute = repeating?.minute,
        daysMask = repeating?.days?.toMask() ?: 0,
        enabled = enabled,
        sunrise = sunrise,
        revision = revision,
        updatedAt = updatedAtEpochMs,
    )
}

internal fun AlarmEntity.toDomain(): Alarm = Alarm(
    id = alarmId,
    kind = AlarmKind.valueOf(kind),
    label = label,
    schedule = toSchedule(),
    enabled = enabled,
    sunrise = sunrise,
    revision = revision,
    updatedAtEpochMs = updatedAt,
)

private fun AlarmEntity.toSchedule(): AlarmSchedule {
    val fires = firesAtEpochMs
    if (fires != null) return AlarmSchedule.OneShot(fires)
    return AlarmSchedule.Repeating(
        hour = requireNotNull(hour) { "repeating alarm without hour: $alarmId" },
        minute = requireNotNull(minute) { "repeating alarm without minute: $alarmId" },
        days = daysMask.toDays(),
    )
}

internal fun Set<DayOfWeek>.toMask(): Int = fold(0) { mask, day -> mask or (1 shl (day.value - 1)) }

internal fun Int.toDays(): Set<DayOfWeek> =
    DayOfWeek.entries.filterTo(linkedSetOf()) { this and (1 shl (it.value - 1)) != 0 }
