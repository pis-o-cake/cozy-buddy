package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.CaptureFormat
import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.protocol.v1.AudioEncoding
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.StreamKind
import app.cozybuddy.protocol.v1.WakeSource
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class UtteranceUplinkTest {

    private val link = FakeHubLink()
    private val capture = FakeAudioCapture()
    private val vad = ScriptedVad()
    private val uplink = UtteranceUplink(link, capture, vad)
    private val frameMs = CaptureFormat.FRAME_MS

    private fun TestScope.feed(frames: Int, value: Int, probability: Float) {
        vad.probability = probability
        repeat(frames) {
            capture.emit(value)
            runCurrent()
        }
    }

    private fun preload(frames: Int) = repeat(frames) { capture.emit(it % 100 + 1) }

    private fun silentFramesToEnd() = (700 + frameMs - 1) / frameMs

    @Test
    fun `utterance start carries format area source and the pre-roll length`() = runTest {
        preload(20)
        val result = async { uplink.run("s1", "living", { true }) }
        runCurrent()

        val start = link.sent.first().utteranceStart
        assertEquals("s1", start.streamId)
        assertEquals("living", start.areaId)
        assertEquals(WakeSource.WAKE_SOURCE_TAP, start.source)
        assertEquals(AudioEncoding.AUDIO_ENCODING_PCM_S16LE, start.format.encoding)
        assertEquals(16_000, start.format.sampleRateHz)
        assertEquals(1, start.format.channels)
        assertEquals(300, start.preRollMs)
        result.cancel()
    }

    @Test
    fun `pre-roll audio precedes live frames and chunk indices are contiguous`() = runTest {
        for (i in 1..20) capture.emit(i)
        val result = async { uplink.run("s1", "living", { true }) }
        runCurrent()

        feed(frames = 3, value = 7777, probability = 0.9f)
        feed(frames = silentFramesToEnd(), value = 8888, probability = 0f)

        assertEquals(UplinkResult.Completed, result.await())
        val chunks = link.sent.audioChunks()

        assertEquals(chunks.indices.toList(), chunks.map { it.index })
        assertTrue(chunks.all { it.kind == StreamKind.STREAM_KIND_UTTERANCE_UP && it.streamId == "s1" })

        // 300 ms pre-roll = 4800 샘플 = 512 짜리 9 개 + 192 짜리 1 개.
        val preRollBytes = ByteArrayOutputStream()
        chunks.take(10).forEach { preRollBytes.write(it.data.toByteArray()) }
        assertEquals(4_800 * 2, preRollBytes.size())
        val firstSample = preRollBytes.toByteArray().let { (it[0].toInt() and 0xFF) or (it[1].toInt() shl 8) }
        // 링에는 마지막 20 프레임(1..20)이 있고, 끝에서 4800 샘플은 프레임 11 의 뒤쪽 192 샘플부터다.
        assertEquals(11, firstSample)

        val live = chunks.drop(10)
        assertEquals(3 + silentFramesToEnd(), live.size)
        assertEquals(CaptureFormat.FRAME_SAMPLES * 2, live.first().data.size())
    }

    @Test
    fun `last flag is only on the final chunk and the end message follows it`() = runTest {
        val result = async { uplink.run("s1", "living", { true }) }
        runCurrent()
        feed(frames = 5, value = 100, probability = 0.9f)
        feed(frames = silentFramesToEnd(), value = 0, probability = 0f)

        assertEquals(UplinkResult.Completed, result.await())

        val chunks = link.sent.audioChunks()
        assertEquals(listOf(true), chunks.map { it.last }.filter { it })
        assertTrue(chunks.last().last)
        assertEquals(Envelope.BodyCase.UTTERANCE_END, link.sent.last().bodyCase)
        assertEquals("s1", link.sent.last().utteranceEnd.streamId)
        assertEquals(Envelope.BodyCase.UTTERANCE_START, link.sent.first().bodyCase)
        assertEquals(Envelope.BodyCase.AUDIO_CHUNK, link.sent[link.sent.size - 2].bodyCase)
    }

    @Test
    fun `no speech timeout stops without sending utterance end`() = runTest {
        val result = async { uplink.run("s1", "living", { true }) }
        runCurrent()

        feed(frames = 8_000 / frameMs, value = 1, probability = 0f)

        assertEquals(UplinkResult.NoSpeech, result.await())
        assertTrue(link.sent.none { it.bodyCase == Envelope.BodyCase.UTTERANCE_END })
    }

    @Test
    fun `nothing is sent when the gate is closed from the start`() = runTest {
        preload(20)
        val result = async { uplink.run("s1", "living", { false }) }
        runCurrent()

        assertEquals(UplinkResult.Aborted, result.await())
        assertTrue(link.sent.isEmpty())
    }

    @Test
    fun `sending stops the moment the gate closes`() = runTest {
        var listening = true
        val result = async { uplink.run("s1", "living", { listening }) }
        runCurrent()
        feed(frames = 4, value = 5, probability = 0.9f)
        val sentWhileOpen = link.sent.size

        listening = false
        feed(frames = 3, value = 5, probability = 0.9f)

        assertEquals(UplinkResult.Aborted, result.await())
        assertEquals(sentWhileOpen, link.sent.size)
        assertTrue(link.sent.none { it.bodyCase == Envelope.BodyCase.UTTERANCE_END })
    }

    @Test
    fun `a link that refuses the start message fails the uplink`() = runTest {
        link.accepting = false

        assertEquals(UplinkResult.LinkFailed, uplink.run("s1", "living", { true }))
    }

    @Test
    fun `a stalled capture fails the uplink instead of hanging`() = runTest {
        val result = async { uplink.run("s1", "living", { true }) }

        assertEquals(UplinkResult.CaptureFailed, result.await())
    }

    @Test
    fun `vad state is reset at the start of every utterance`() = runTest {
        val result = async { uplink.run("s1", "living", { true }) }
        runCurrent()
        feed(frames = 2, value = 1, probability = 0.9f)
        feed(frames = silentFramesToEnd(), value = 0, probability = 0f)
        result.await()

        assertEquals(1, vad.resets)
    }
}
