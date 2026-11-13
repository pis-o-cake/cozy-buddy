package app.cozybuddy.hub.domain.model

import java.time.Instant

/** 스누즈 중인 알람과 다시 울릴 시각. 알람 데이터가 아니라 울림 상태이므로 revision 과 무관하다. */
data class Snooze(val alarmId: String, val fireAt: Instant)
