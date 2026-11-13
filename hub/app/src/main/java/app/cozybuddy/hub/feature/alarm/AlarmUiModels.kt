package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.format.TextStyle
import java.util.Locale

/** 목록 항목의 부가 설명. 문구는 리소스가 필요하므로 UI 에서 문자열로 바꾼다. */
sealed interface AlarmDetail {
    data object EveryDay : AlarmDetail
    data class Days(val text: String) : AlarmDetail
    data class Date(val text: String) : AlarmDetail
}

data class AlarmItemUi(
    val id: String,
    val kind: AlarmKind,
    val label: String,
    val timeText: String,
    val detail: AlarmDetail,
    val enabled: Boolean,
)

data class DayOption(val day: DayOfWeek, val shortName: String)

data class AlarmListUiState(
    val items: List<AlarmItemUi> = emptyList(),
    val exactAlarmGranted: Boolean = true,
    val is24Hour: Boolean = true,
    val dayOptions: List<DayOption> = emptyList(),
)

data class AlarmRingUiState(
    val visible: Boolean = false,
    val kind: AlarmKind = AlarmKind.ALARM,
    val label: String = "",
    val timeText: String = "",
    val snoozeMinutes: Int = 0,
)

internal fun formatClock(time: LocalTime, is24Hour: Boolean, locale: Locale): String {
    val pattern = if (is24Hour) "HH:mm" else "h:mm a"
    return time.format(DateTimeFormatter.ofPattern(pattern, locale))
}

internal fun formatEpoch(epochMs: Long, zone: ZoneId, is24Hour: Boolean, locale: Locale): String =
    formatClock(Instant.ofEpochMilli(epochMs).atZone(zone).toLocalTime(), is24Hour, locale)

internal fun Alarm.toItemUi(zone: ZoneId, is24Hour: Boolean, locale: Locale): AlarmItemUi {
    val (timeText, detail) = when (val s = schedule) {
        is AlarmSchedule.Repeating -> formatClock(LocalTime.of(s.hour, s.minute), is24Hour, locale) to repeatingDetail(s, locale)
        is AlarmSchedule.OneShot -> {
            val date = Instant.ofEpochMilli(s.firesAtEpochMs).atZone(zone).toLocalDate()
            formatEpoch(s.firesAtEpochMs, zone, is24Hour, locale) to
                AlarmDetail.Date(date.format(DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(locale)))
        }
    }
    return AlarmItemUi(id, kind, label, timeText, detail, enabled)
}

private fun repeatingDetail(schedule: AlarmSchedule.Repeating, locale: Locale): AlarmDetail {
    if (schedule.days.isEmpty() || schedule.days.size == DayOfWeek.entries.size) return AlarmDetail.EveryDay
    return AlarmDetail.Days(schedule.days.sorted().joinToString(" ") { it.getDisplayName(TextStyle.SHORT, locale) })
}
