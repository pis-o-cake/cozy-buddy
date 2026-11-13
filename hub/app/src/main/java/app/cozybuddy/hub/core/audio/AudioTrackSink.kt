package app.cozybuddy.hub.core.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import timber.log.Timber

/**
 * `USAGE_ASSISTANT` 로 PCM16 모노를 스트리밍 재생하는 [PcmSink].
 *
 * 알람은 `USAGE_ALARM` 으로 따로 재생하므로 이 싱크와 섞이지 않는다.
 */
class AudioTrackSink(sampleRateHz: Int) : PcmSink {

    private val track: AudioTrack
    private val lock = Any()
    private var released = false

    init {
        val minBytes = AudioTrack.getMinBufferSize(
            sampleRateHz,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferBytes = maxOf(minBytes, sampleRateHz * BYTES_PER_FRAME * BUFFER_MS / MS_PER_SECOND)
        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(sampleRateHz)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(bufferBytes)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
    }

    override fun play() {
        synchronized(lock) {
            if (released) return
            track.play()
        }
    }

    // 블로킹 쓰기이므로 락을 잡지 않는다. release 가 다른 스레드에서 이 쓰기를 깨워야 한다.
    override fun write(data: ByteArray, offset: Int, length: Int): Int = try {
        track.write(data, offset, length)
    } catch (e: IllegalStateException) {
        Timber.w(e, "AudioTrack write after release")
        -1
    }

    override fun playedFrames(): Long = synchronized(lock) {
        if (released) return Long.MAX_VALUE
        track.playbackHeadPosition.toLong() and UNSIGNED_INT_MASK
    }

    override fun release() {
        synchronized(lock) {
            if (released) return
            released = true
            runCatching {
                track.pause()
                track.flush()
            }
            track.release()
        }
    }

    private companion object {
        const val BYTES_PER_FRAME = 2
        const val MS_PER_SECOND = 1_000
        const val BUFFER_MS = 500
        const val UNSIGNED_INT_MASK = 0xFFFFFFFFL
    }
}
