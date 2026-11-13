package app.cozybuddy.hub.domain.scheduling

import java.time.Instant

/** 알람 예약의 플랫폼 추상화. 알람 하나당 일반 예약 하나와 스누즈 예약 하나를 가진다. */
interface AlarmScheduler {

    /** 정각 알람을 걸 수 있는 권한 상태. false 면 예약이 무시되므로 UI 가 설정 안내를 띄운다. */
    fun canScheduleExact(): Boolean

    /** 같은 [alarmId] 의 기존 일반 예약을 [triggerAt] 으로 교체한다. */
    fun schedule(alarmId: String, triggerAt: Instant)

    /** 일반 예약과 별개 슬롯이라 반복 알람의 다음 예약을 덮어쓰지 않는다. */
    fun scheduleSnooze(alarmId: String, triggerAt: Instant)

    /** 일반·스누즈 예약을 모두 취소한다. */
    fun cancel(alarmId: String)
}
