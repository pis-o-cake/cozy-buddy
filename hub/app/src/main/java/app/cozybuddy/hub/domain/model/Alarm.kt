package app.cozybuddy.hub.domain.model

import java.time.DayOfWeek

enum class AlarmKind { ALARM, TIMER }

sealed interface AlarmSchedule {
    /** 절대 시각에 한 번만 울린다. 타이머도 이 형태다. */
    data class OneShot(val firesAtEpochMs: Long) : AlarmSchedule

    /**
     * 허브 로컬 벽시계 기준 반복.
     *
     * [days] 가 비어 있으면 매일로 해석한다.
     */
    data class Repeating(
        val hour: Int,
        val minute: Int,
        val days: Set<DayOfWeek>,
    ) : AlarmSchedule
}

/**
 * 알람·타이머 한 건. `cozy.v1.Alarm` 과 같은 의미를 갖지만 저장·전송 형식과 무관하다.
 *
 * [revision] 은 허브가 저장할 때만 부여한다. 호출자가 넘긴 값은 저장 시 무시된다.
 */
data class Alarm(
    val id: String,
    val kind: AlarmKind,
    val label: String,
    val schedule: AlarmSchedule,
    val enabled: Boolean,
    val sunrise: Boolean = false,
    val revision: Long = 0L,
    val updatedAtEpochMs: Long = 0L,
)
