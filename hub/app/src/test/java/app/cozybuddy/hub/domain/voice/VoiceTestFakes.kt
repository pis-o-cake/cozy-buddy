package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.AudioCapture
import app.cozybuddy.hub.core.audio.CaptureFormat
import app.cozybuddy.hub.core.audio.FrameDistributor
import app.cozybuddy.hub.core.audio.FrameSubscription
import app.cozybuddy.hub.core.audio.PlaybackDrained
import app.cozybuddy.hub.core.audio.PlaybackSession
import app.cozybuddy.hub.core.audio.ResponsePlayer
import app.cozybuddy.hub.core.audio.Vad
import app.cozybuddy.protocol.v1.AudioChunk
import app.cozybuddy.protocol.v1.CancelReason
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.StreamKind
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow

/** 실제 마이크 없이 프레임을 밀어 넣는 캡처. */
class FakeAudioCapture : AudioCapture {
    val distributor = FrameDistributor()
    override val running = MutableStateFlow(true)

    override fun start(): Boolean = true.also { running.value = true }

    override fun stop() {
        running.value = false
    }

    override fun subscribe(preRollMs: Int): FrameSubscription = distributor.subscribe(preRollMs)

    fun emit(value: Int) = distributor.publish(frame(value))

    companion object {
        fun frame(value: Int) = ShortArray(CaptureFormat.FRAME_SAMPLES) { value.toShort() }
    }
}

/** 정해 둔 확률을 돌려주는 VAD. 확률은 테스트가 바꿀 수 있다. */
class ScriptedVad(var probability: Float = 0f) : Vad {
    private val script = ArrayDeque<Float>()
    var resets = 0

    fun script(vararg values: Float) = script.addAll(values.toList())

    override suspend fun speechProbability(frame: ShortArray): Float = script.removeFirstOrNull() ?: probability

    override fun reset() {
        resets++
    }
}

class FakeResponsePlayer : ResponsePlayer {
    override val current = MutableStateFlow<PlaybackSession?>(null)
    override val drained = MutableSharedFlow<PlaybackDrained>(extraBufferCapacity = 8)
    val calls = mutableListOf<String>()

    override fun start(streamId: String, kind: StreamKind, sampleRateHz: Int) {
        calls += "start:$streamId:${kind.name}:$sampleRateHz"
        current.value = PlaybackSession(streamId, kind)
    }

    override fun write(streamId: String, index: Int, data: ByteArray) {
        calls += "write:$streamId:$index"
    }

    override fun finish(streamId: String) {
        calls += "finish:$streamId"
    }

    override fun stop() {
        calls += "stop"
        current.value = null
    }

    fun finishPlayback(streamId: String, kind: StreamKind = StreamKind.STREAM_KIND_RESPONSE_DOWN) {
        current.value = null
        drained.tryEmit(PlaybackDrained(streamId, kind))
    }
}

class FakeAlarmRinging : AlarmRingingSource {
    override val ringing: MutableStateFlow<Boolean> = MutableStateFlow(false)
}

class SequentialIds : StreamIdGenerator {
    private var next = 1
    override fun next(): String = "u${next++}"
}

internal val Envelope.cancelReason: CancelReason get() = utteranceCancel.reason
internal fun List<Envelope>.ofCase(case: Envelope.BodyCase) = filter { it.bodyCase == case }
internal fun List<Envelope>.audioChunks(): List<AudioChunk> = ofCase(Envelope.BodyCase.AUDIO_CHUNK).map { it.audioChunk }
