package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.AudioCapture
import app.cozybuddy.hub.core.audio.CaptureFormat
import app.cozybuddy.hub.core.audio.EndOfSpeechDetector
import app.cozybuddy.hub.core.audio.Vad
import app.cozybuddy.hub.core.audio.VadConfig
import app.cozybuddy.hub.core.audio.VadDecision
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.protocol.v1.WakeSource
import com.google.protobuf.ByteString
import kotlin.math.sqrt
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.withTimeoutOrNull
import timber.log.Timber

/**
 * @property preRollMs 탭 이전 오디오를 얼마나 앞에 붙일지. 링버퍼는 더 길게 들고 있지만, 탭 이전 방 소리를
 * 서버로 보내는 것은 최소화해야 하므로 손가락이 닿고 말을 시작하기까지의 지연만 덮는다.
 */
data class UplinkConfig(
    val source: WakeSource = WakeSource.WAKE_SOURCE_TAP,
    val preRollMs: Int = DEFAULT_PRE_ROLL_MS,
    /** 프레임이 이 시간 동안 오지 않으면 캡처가 죽은 것으로 본다. */
    val frameStallMs: Long = 1_000L,
) {
    companion object {
        const val DEFAULT_PRE_ROLL_MS = 300
    }
}

sealed interface UplinkResult {
    /** 발화 종료까지 전송했고 UtteranceEnd 도 보냈다. */
    data object Completed : UplinkResult

    /** 말이 시작되지 않은 채 시간이 지났다. UtteranceEnd 는 보내지 않았다. */
    data object NoSpeech : UplinkResult

    /** LISTENING 이 아니게 되어 전송을 멈췄다. 이후 아무것도 보내지 않는다. */
    data object Aborted : UplinkResult

    /** 연결이 메시지를 받지 않았다. */
    data object LinkFailed : UplinkResult

    /** 캡처 정지나 VAD 오류로 프레임을 더 받을 수 없다. */
    data object CaptureFailed : UplinkResult
}

/**
 * 탭 한 번에 대한 발화 업링크 한 건: UtteranceStart → pre-roll + 라이브 프레임 → UtteranceEnd.
 *
 * IMPORTANT: 모든 전송 직전에 [canSend] 를 확인한다. 상태 기계가 LISTENING 이 아니면 한 바이트도 보내지 않는다.
 * 마지막 오디오 조각에 `last` 를 싣기 위해 조각 하나를 한 박자 늦춰 보낸다.
 */
class UtteranceUplink(
    private val link: HubLink,
    private val capture: AudioCapture,
    private val vad: Vad,
    private val config: UplinkConfig = UplinkConfig(),
    private val vadConfig: VadConfig = VadConfig(),
) {

    suspend fun run(
        streamId: String,
        areaId: String,
        canSend: () -> Boolean,
        onLevel: (Float) -> Unit = {},
        source: WakeSource = config.source,
        preRollMs: Int = config.preRollMs,
    ): UplinkResult {
        val subscription = capture.subscribe(preRollMs)
        try {
            vad.reset()
            val sentPreRollMs = subscription.preRoll.size * MS_PER_SECOND / CaptureFormat.SAMPLE_RATE_HZ
            if (!canSend()) return UplinkResult.Aborted
            val started = link.send(VoiceMessages.utteranceStart(streamId, areaId, source, sentPreRollMs))
            if (!started) return UplinkResult.LinkFailed

            val writer = ChunkWriter(streamId, canSend)
            for (piece in subscription.preRoll.asList().chunked(CaptureFormat.FRAME_SAMPLES)) {
                writer.push(piece.toShortArray())?.let { return it }
            }

            val detector = EndOfSpeechDetector(vadConfig)
            while (true) {
                val frame = withTimeoutOrNull(config.frameStallMs) { subscription.live.receiveCatching().getOrNull() }
                if (frame == null) {
                    Timber.w("Capture stalled during utterance")
                    return UplinkResult.CaptureFailed
                }
                onLevel(level(frame))
                writer.push(frame)?.let { return it }
                val probability = try {
                    vad.speechProbability(frame)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Timber.e(e, "VAD inference failed")
                    return UplinkResult.CaptureFailed
                }
                when (detector.onFrame(probability)) {
                    VadDecision.CONTINUE -> Unit
                    VadDecision.NO_SPEECH -> return UplinkResult.NoSpeech
                    VadDecision.END_OF_SPEECH, VadDecision.MAX_LENGTH -> return finish(streamId, writer, canSend)
                }
            }
        } finally {
            subscription.close()
        }
    }

    private fun finish(streamId: String, writer: ChunkWriter, canSend: () -> Boolean): UplinkResult {
        writer.flushLast()?.let { return it }
        if (!canSend()) return UplinkResult.Aborted
        if (!link.send(VoiceMessages.utteranceEnd(streamId))) return UplinkResult.LinkFailed
        return UplinkResult.Completed
    }

    private fun level(frame: ShortArray): Float {
        var sum = 0.0
        for (sample in frame) sum += sample.toDouble() * sample
        val rms = sqrt(sum / frame.size).toFloat() / Short.MAX_VALUE
        return (rms * LEVEL_GAIN).coerceAtMost(1f)
    }

    /** 오디오 조각을 순번대로 보낸다. 조각 하나를 쥐고 있다가 다음 조각이 오면 `last=false` 로 내보낸다. */
    private inner class ChunkWriter(private val streamId: String, private val canSend: () -> Boolean) {
        private var index = 0
        private var pending: ByteString? = null

        /** 전송을 계속할 수 없으면 그 사유를, 아니면 null. */
        fun push(samples: ShortArray): UplinkResult? {
            val previous = pending
            pending = VoiceMessages.pcmBytes(samples)
            return if (previous == null) null else send(previous, last = false)
        }

        fun flushLast(): UplinkResult? = send(pending ?: ByteString.EMPTY, last = true).also { pending = null }

        private fun send(data: ByteString, last: Boolean): UplinkResult? {
            if (!canSend()) return UplinkResult.Aborted
            val sent = link.send(VoiceMessages.audioChunk(streamId, index, data, last))
            if (!sent) return UplinkResult.LinkFailed
            index++
            return null
        }
    }

    private companion object {
        const val MS_PER_SECOND = 1_000
        const val LEVEL_GAIN = 6f
    }
}
