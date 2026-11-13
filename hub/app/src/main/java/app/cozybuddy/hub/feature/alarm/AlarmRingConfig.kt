package app.cozybuddy.hub.feature.alarm

import java.time.Duration

/** 알람 볼륨 상승 곡선과 자동 종료 시간. */
object AlarmRingConfig {
    val RAMP_DURATION: Duration = Duration.ofSeconds(30)
    val AUTO_STOP: Duration = Duration.ofMinutes(10)
    const val START_VOLUME = 0.1f
    const val RAMP_STEP_MS = 500L

    /** 경과 시간에 대한 선형 볼륨. [START_VOLUME] 에서 시작해 램프 끝에 1.0 에 닿고 그 뒤로 유지한다. */
    fun volumeAt(elapsedMs: Long): Float {
        val rampMs = RAMP_DURATION.toMillis()
        if (elapsedMs <= 0) return START_VOLUME
        if (elapsedMs >= rampMs) return 1f
        return START_VOLUME + (1f - START_VOLUME) * elapsedMs / rampMs
    }
}
