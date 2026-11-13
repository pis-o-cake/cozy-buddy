package app.cozybuddy.hub.core.audio

import app.cozybuddy.protocol.v1.StreamKind
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

/** 지금 재생 중인 서버 스트림. 응답과 방송은 같은 플레이어를 쓰지만 종류로 구분한다. */
data class PlaybackSession(val streamId: String, val kind: StreamKind)

/** 재생이 끝까지 소진돼 자연 종료된 스트림. [ResponsePlayer.stop] 으로 끊긴 경우에는 나오지 않는다. */
data class PlaybackDrained(val streamId: String, val kind: StreamKind)

/**
 * 서버가 내려준 PCM16 모노 스트림을 도착하는 대로 재생한다. 동시에 스트림 하나만 재생한다.
 */
interface ResponsePlayer {
    val current: StateFlow<PlaybackSession?>
    val drained: SharedFlow<PlaybackDrained>

    /** 새 스트림을 연다. 재생 중인 스트림이 있으면 즉시 끊는다. */
    fun start(streamId: String, kind: StreamKind, sampleRateHz: Int)

    /** 청크를 이어 붙인다. 현재 스트림이 아니거나 이미 지난 순번이면 버린다. */
    fun write(streamId: String, index: Int, data: ByteArray)

    /** 더 올 청크가 없음을 알린다. 남은 오디오를 다 재생하면 [drained] 가 나온다. */
    fun finish(streamId: String)

    /** 버퍼에 남은 오디오를 버리고 즉시 멈춘다. */
    fun stop()
}

/** `AudioTrack` 을 가린 출력. 테스트는 가짜로 바꿔 청크 처리만 검증한다. */
interface PcmSink {
    /** 재생을 시작한다. 이후 [write] 한 데이터가 소리가 된다. */
    fun play()

    /** 블로킹 쓰기. 쓴 바이트 수를 돌려주고, 이미 해제됐으면 음수. */
    fun write(data: ByteArray, offset: Int, length: Int): Int

    /** 지금까지 실제로 재생된 프레임 수. */
    fun playedFrames(): Long

    /** 남은 버퍼를 버리고 멈춘 뒤 해제한다. 다른 스레드에서 불려도 안전해야 한다. */
    fun release()
}

fun interface PcmSinkFactory {
    fun create(sampleRateHz: Int): PcmSink
}
