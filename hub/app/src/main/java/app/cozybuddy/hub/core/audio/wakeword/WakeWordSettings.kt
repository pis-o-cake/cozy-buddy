package app.cozybuddy.hub.core.audio.wakeword

import kotlinx.coroutines.flow.Flow

/**
 * 호출어 사용자 설정.
 *
 * @property enabled 꺼져 있으면 엔진을 올리지 않고 마이크 프레임도 소비하지 않는다.
 * @property sensitivity 0..1. 높을수록 잘 깨어나지만 오탐도 늘어난다. [WakeSensitivity.toThreshold] 로 문턱이 된다.
 */
data class WakeWordPrefs(
    val enabled: Boolean = true,
    val sensitivity: Float = WakeSensitivity.DEFAULT,
)

interface WakeWordSettings {
    val prefs: Flow<WakeWordPrefs>

    suspend fun setEnabled(enabled: Boolean)

    suspend fun setSensitivity(sensitivity: Float)
}
