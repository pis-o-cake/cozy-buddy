package app.cozybuddy.hub.core.audio

import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.ReceiveChannel

/** pre-roll 과 그 직후부터 끊김 없이 이어지는 라이브 프레임 구독. [close] 로 해지한다. */
class FrameSubscription internal constructor(
    val preRoll: ShortArray,
    val live: ReceiveChannel<ShortArray>,
    private val onClose: () -> Unit,
) : AutoCloseable {
    override fun close() = onClose()
}

/**
 * 캡처 스레드가 만든 프레임을 링버퍼에 쌓고 구독자에게 나눠 준다.
 *
 * IMPORTANT: 링버퍼 스냅샷과 구독 등록을 같은 락 안에서 하므로 pre-roll 과 라이브 프레임 사이에
 * 샘플이 빠지거나 겹치지 않는다. 락은 메모리 복사 동안만 잡는다.
 *
 * 느린 구독자가 캡처를 막지 않도록 구독 채널은 유한 용량이며, 넘치면 가장 오래된 프레임을 버린다.
 */
class FrameDistributor(
    ringCapacitySamples: Int = CaptureFormat.msToSamples(CaptureFormat.RING_BUFFER_MS),
    private val subscriberBufferFrames: Int = SUBSCRIBER_BUFFER_FRAMES,
) {
    private val lock = Any()
    private val ring = PcmRingBuffer(ringCapacitySamples)
    private val subscribers = CopyOnWriteArrayList<Channel<ShortArray>>()

    fun publish(frame: ShortArray) {
        // 링 기록과 구독자 전달을 한 락에 묶어 subscribe 의 스냅샷과 순서가 어긋나지 않게 한다.
        synchronized(lock) {
            ring.write(frame)
            for (channel in subscribers) channel.trySend(frame)
        }
    }

    fun subscribe(preRollMs: Int): FrameSubscription {
        val channel = Channel<ShortArray>(subscriberBufferFrames, BufferOverflow.DROP_OLDEST)
        val preRoll = synchronized(lock) {
            val snapshot = ring.latest(CaptureFormat.msToSamples(preRollMs))
            subscribers += channel
            snapshot
        }
        return FrameSubscription(preRoll, channel) {
            subscribers -= channel
            channel.close()
        }
    }

    /** 캡처가 멈추면 이전 세션의 오디오가 다음 pre-roll 에 섞이지 않도록 비운다. */
    fun reset() = synchronized(lock) { ring.clear() }

    private companion object {
        const val SUBSCRIBER_BUFFER_FRAMES = 128
    }
}
