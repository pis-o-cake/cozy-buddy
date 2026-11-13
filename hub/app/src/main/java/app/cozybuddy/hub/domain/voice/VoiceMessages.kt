package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.CaptureFormat
import app.cozybuddy.protocol.v1.AudioEncoding
import app.cozybuddy.protocol.v1.CancelReason
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.StreamKind
import app.cozybuddy.protocol.v1.WakeSource
import app.cozybuddy.protocol.v1.audioChunk
import app.cozybuddy.protocol.v1.audioFormat
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.utteranceCancel
import app.cozybuddy.protocol.v1.utteranceEnd
import app.cozybuddy.protocol.v1.utteranceStart
import com.google.protobuf.ByteString
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** 발화 업링크 메시지 생성. seq 는 연결 계층이 채우므로 여기서는 비워 둔다. */
internal object VoiceMessages {

    fun utteranceStart(streamId: String, areaId: String, source: WakeSource, preRollMs: Int): Envelope = envelope {
        utteranceStart = utteranceStart {
            this.streamId = streamId
            format = audioFormat {
                encoding = AudioEncoding.AUDIO_ENCODING_PCM_S16LE
                sampleRateHz = CaptureFormat.SAMPLE_RATE_HZ
                channels = 1
            }
            this.areaId = areaId
            this.source = source
            this.preRollMs = preRollMs
        }
    }

    fun audioChunk(streamId: String, index: Int, data: ByteString, last: Boolean): Envelope = envelope {
        audioChunk = audioChunk {
            kind = StreamKind.STREAM_KIND_UTTERANCE_UP
            this.streamId = streamId
            this.index = index
            this.data = data
            this.last = last
        }
    }

    fun utteranceEnd(streamId: String): Envelope = envelope {
        utteranceEnd = utteranceEnd { this.streamId = streamId }
    }

    fun utteranceCancel(streamId: String, reason: CancelReason): Envelope = envelope {
        utteranceCancel = utteranceCancel {
            this.streamId = streamId
            this.reason = reason
        }
    }

    /** PCM16 샘플을 little-endian 바이트로 직렬화한다. */
    fun pcmBytes(samples: ShortArray): ByteString {
        val buffer = ByteBuffer.allocate(samples.size * CaptureFormat.BYTES_PER_SAMPLE).order(ByteOrder.LITTLE_ENDIAN)
        buffer.asShortBuffer().put(samples)
        return ByteString.copyFrom(buffer.array())
    }
}
