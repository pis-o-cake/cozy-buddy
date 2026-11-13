package app.cozybuddy.hub.core.audio

import app.cozybuddy.protocol.v1.StreamKind
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import timber.log.Timber

/**
 * [PcmSink] 로 스트림을 재생하는 [ResponsePlayer].
 *
 * 처음 [jitterMs] 만큼 모이거나 스트림이 끝나면 재생을 시작한다. 네트워크 지터로 첫 소리가 끊기는 것을 막으려는 버퍼다.
 * 쓰기는 블로킹이라 [dispatcher] 는 이 플레이어 전용 스레드여야 한다.
 */
class StreamingResponsePlayer(
    private val sinkFactory: PcmSinkFactory,
    dispatcher: CoroutineDispatcher,
    private val jitterMs: Int = DEFAULT_JITTER_MS,
) : ResponsePlayer {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val lock = Any()
    private var active: ActiveStream? = null

    private val _current = MutableStateFlow<PlaybackSession?>(null)
    override val current: StateFlow<PlaybackSession?> = _current.asStateFlow()

    private val _drained = MutableSharedFlow<PlaybackDrained>(extraBufferCapacity = DRAINED_BUFFER)
    override val drained: SharedFlow<PlaybackDrained> = _drained.asSharedFlow()

    private class ActiveStream(val streamId: String, val kind: StreamKind, val sampleRateHz: Int) {
        val chunks = Channel<ByteArray>(Channel.UNLIMITED)
        var nextIndex = 0
        @Volatile var sink: PcmSink? = null
        var job: Job? = null
    }

    override fun start(streamId: String, kind: StreamKind, sampleRateHz: Int) {
        val stream = ActiveStream(streamId, kind, sampleRateHz)
        synchronized(lock) {
            stopLocked()
            active = stream
            _current.value = PlaybackSession(streamId, kind)
            stream.job = scope.launch { play(stream) }
        }
        Timber.d("Playback stream opened: kind=%s rate=%d", kind, sampleRateHz)
    }

    override fun write(streamId: String, index: Int, data: ByteArray) {
        synchronized(lock) {
            val stream = active?.takeIf { it.streamId == streamId } ?: return
            if (index < stream.nextIndex) {
                Timber.w("Dropping stale audio chunk: index=%d expected=%d", index, stream.nextIndex)
                return
            }
            if (index > stream.nextIndex) {
                Timber.w("Audio chunk gap: index=%d expected=%d", index, stream.nextIndex)
            }
            stream.nextIndex = index + 1
            stream.chunks.trySend(data)
        }
    }

    override fun finish(streamId: String) {
        synchronized(lock) {
            active?.takeIf { it.streamId == streamId }?.chunks?.close()
        }
    }

    override fun stop() {
        synchronized(lock) { stopLocked() }
    }

    private fun stopLocked() {
        val stream = active ?: return
        active = null
        _current.value = null
        stream.chunks.close()
        stream.job?.cancel()
        // 쓰기에서 블로킹 중인 스레드는 취소로 깨지 않으므로, 싱크를 직접 해제해 즉시 끊는다.
        stream.sink?.release()
        Timber.d("Playback stopped")
    }

    private suspend fun play(stream: ActiveStream) {
        val sink = sinkFactory.create(stream.sampleRateHz)
        stream.sink = sink
        // stop 이 싱크 생성 전에 불렸다면 아무도 해제하지 않으므로 여기서 직접 정리한다.
        if (synchronized(lock) { active !== stream }) {
            sink.release()
            return
        }
        try {
            var framesWritten = 0L
            var started = false
            val head = ArrayList<ByteArray>()
            var headBytes = 0
            val jitterBytes = stream.sampleRateHz * BYTES_PER_FRAME * jitterMs / MS_PER_SECOND
            var carry: Byte? = null

            suspend fun startPlayback() {
                started = true
                sink.play()
                for (queued in head) framesWritten += writeAll(sink, queued) / BYTES_PER_FRAME
                head.clear()
            }

            for (raw in stream.chunks) {
                val aligned = alignToSample(carry, raw)
                carry = aligned.second
                val chunk = aligned.first
                if (chunk.isEmpty()) continue
                if (started) {
                    framesWritten += writeAll(sink, chunk) / BYTES_PER_FRAME
                    continue
                }
                head += chunk
                headBytes += chunk.size
                if (headBytes >= jitterBytes) startPlayback()
            }
            if (!started) startPlayback()

            awaitDrained(sink, framesWritten, stream.sampleRateHz)
            finishStream(stream)
        } finally {
            sink.release()
        }
    }

    private suspend fun awaitDrained(sink: PcmSink, framesWritten: Long, sampleRateHz: Int) {
        val remainingMs = (framesWritten - sink.playedFrames()).coerceAtLeast(0) * MS_PER_SECOND / sampleRateHz
        val deadline = remainingMs + DRAIN_GRACE_MS
        var waited = 0L
        while (sink.playedFrames() < framesWritten && waited < deadline) {
            delay(DRAIN_POLL_MS)
            waited += DRAIN_POLL_MS
        }
    }

    private fun finishStream(stream: ActiveStream) {
        synchronized(lock) {
            if (active !== stream) return
            active = null
            _current.value = null
        }
        _drained.tryEmit(PlaybackDrained(stream.streamId, stream.kind))
        Timber.d("Playback drained")
    }

    /** 블로킹 쓰기를 끝까지 반복한다. 싱크가 해제돼 음수를 주면 지금까지 쓴 만큼만 돌려준다. */
    private fun writeAll(sink: PcmSink, data: ByteArray): Int {
        var offset = 0
        while (offset < data.size) {
            val written = sink.write(data, offset, data.size - offset)
            if (written <= 0) break
            offset += written
        }
        return offset
    }

    /** PCM16 은 2바이트 단위여야 한다. 청크 경계에서 반쪽 샘플이 나오면 다음 청크로 넘긴다. */
    private fun alignToSample(carry: Byte?, chunk: ByteArray): Pair<ByteArray, Byte?> {
        val joined = if (carry == null) chunk else byteArrayOf(carry) + chunk
        if (joined.size % BYTES_PER_FRAME == 0) return joined to null
        return joined.copyOf(joined.size - 1) to joined.last()
    }

    companion object {
        const val DEFAULT_JITTER_MS = 120
        private const val BYTES_PER_FRAME = 2
        private const val MS_PER_SECOND = 1_000
        private const val DRAINED_BUFFER = 8
        private const val DRAIN_POLL_MS = 20L
        private const val DRAIN_GRACE_MS = 1_000L
    }
}
