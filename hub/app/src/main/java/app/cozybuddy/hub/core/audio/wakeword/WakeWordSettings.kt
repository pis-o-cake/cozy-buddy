package app.cozybuddy.hub.core.audio.wakeword

import kotlinx.coroutines.flow.Flow

/**
 * 호출어 사용자 설정.
 *
 * @property enabled 꺼져 있으면 엔진을 올리지 않고 마이크 프레임도 소비하지 않는다.
 * @property sensitivity 0..1. 높을수록 잘 깨어나지만 오탐도 늘어난다. [WakeSensitivity.toThreshold] 로 문턱이 된다.
 * @property keyword 호출어. 바꾸면 감도는 기본 위치로 돌아간다(키워드마다 문턱 범위가 다르다).
 */
data class WakeWordPrefs(
    val enabled: Boolean = true,
    val sensitivity: Float = WakeSensitivity.DEFAULT,
    val keyword: WakeWordKeyword = WakeWordKeyword.HEY_JARVIS,
)

interface WakeWordSettings {
    val prefs: Flow<WakeWordPrefs>

    suspend fun setEnabled(enabled: Boolean)

    suspend fun setSensitivity(sensitivity: Float)

    /** 호출어를 저장하고 감도를 [WakeSensitivity.DEFAULT] 로 되돌린다. */
    suspend fun setKeyword(keyword: WakeWordKeyword)
}
