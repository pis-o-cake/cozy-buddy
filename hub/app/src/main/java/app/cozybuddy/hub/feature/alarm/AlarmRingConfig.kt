package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.domain.model.AlarmKind
import java.time.Duration

/**
 * 울림 방식. 알람·타이머는 [looping] 반복음을 [ramp] 로 서서히 키우고, 리마인더는 짧은 알림음을 [chimeRepeats] 번만 낸다.
 *
 * @property autoStop 이 시간이 지나면 스스로 멈춘다.
 * @property announcesLabel 울릴 때 서버에 [app.cozybuddy.protocol.v1.ReminderFired] 를 보내 내용을 음성으로 받는다.
 */
data class RingProfile(
    val looping: Boolean,
    val chimeRepeats: Int,
    val chimeGap: Duration,
    val startVolume: Float,
    val ramp: Boolean,
    val autoStop: Duration,
    val announcesLabel: Boolean,
)

/** 알람 볼륨 상승 곡선과 자동 종료 시간. */
object AlarmRingConfig {
    val RAMP_DURATION: Duration = Duration.ofSeconds(30)
    val AUTO_STOP: Duration = Duration.ofMinutes(10)
    const val START_VOLUME = 0.1f
    const val RAMP_STEP_MS = 500L

    /** 리마인더 알림음 반복 횟수와 간격. 음성 안내가 시작되면 남은 반복은 취소된다. */
    const val REMINDER_CHIME_REPEATS = 3
    val REMINDER_CHIME_GAP: Duration = Duration.ofSeconds(4)
    const val REMINDER_VOLUME = 0.8f
    val REMINDER_AUTO_STOP: Duration = Duration.ofSeconds(60)

    /** 음성 안내 재생이 끝난 뒤 화면을 더 보여 주는 시간. 이 안에 정지·스누즈를 누를 수 있다. */
    val REMINDER_LINGER_AFTER_SPEECH: Duration = Duration.ofSeconds(8)

    private val ALARM_PROFILE = RingProfile(
        looping = true,
        chimeRepeats = 0,
        chimeGap = Duration.ZERO,
        startVolume = START_VOLUME,
        ramp = true,
        autoStop = AUTO_STOP,
        announcesLabel = false,
    )

    private val REMINDER_PROFILE = RingProfile(
        looping = false,
        chimeRepeats = REMINDER_CHIME_REPEATS,
        chimeGap = REMINDER_CHIME_GAP,
        startVolume = REMINDER_VOLUME,
        ramp = false,
        autoStop = REMINDER_AUTO_STOP,
        announcesLabel = true,
    )

    /** 종류별 울림 방식. 리마인더만 짧은 알림음과 음성 안내를 쓴다. */
    fun profileFor(kind: AlarmKind): RingProfile = if (kind == AlarmKind.REMINDER) REMINDER_PROFILE else ALARM_PROFILE

    /** 경과 시간에 대한 선형 볼륨. [START_VOLUME] 에서 시작해 램프 끝에 1.0 에 닿고 그 뒤로 유지한다. */
    fun volumeAt(elapsedMs: Long): Float {
        val rampMs = RAMP_DURATION.toMillis()
        if (elapsedMs <= 0) return START_VOLUME
        if (elapsedMs >= rampMs) return 1f
        return START_VOLUME + (1f - START_VOLUME) * elapsedMs / rampMs
    }
}
