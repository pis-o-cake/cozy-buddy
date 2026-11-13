package app.cozybuddy.hub.core.audio

import kotlinx.coroutines.flow.StateFlow

/**
 * 앱 전체가 공유하는 마이크 캡처. 구현은 `AudioRecord` 를 하나만 연다.
 *
 * 포그라운드 서비스가 수명을 관리한다. 서비스가 없으면 캡처도 없다.
 */
interface AudioCapture {
    /** 마이크가 실제로 열려 프레임을 만드는 중인지. */
    val running: StateFlow<Boolean>

    /** 캡처를 시작한다. 권한이 없거나 장치를 열 수 없으면 false. 이미 돌고 있으면 true. */
    fun start(): Boolean

    fun stop()

    /**
     * 프레임을 구독한다. 반환된 [FrameSubscription.preRoll] 은 최근 [preRollMs] 만큼의 과거 오디오이고,
     * [FrameSubscription.live] 는 그 직후부터의 프레임이다. 캡처가 멈춰 있으면 빈 pre-roll 과 빈 채널을 준다.
     */
    fun subscribe(preRollMs: Int): FrameSubscription
}
