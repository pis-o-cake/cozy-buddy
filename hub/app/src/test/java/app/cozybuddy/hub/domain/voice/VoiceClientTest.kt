package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.CaptureFormat
import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.protocol.v1.AudioEncoding
import app.cozybuddy.protocol.v1.CancelReason
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.StreamKind
import app.cozybuddy.protocol.v1.WakeSource
import app.cozybuddy.protocol.v1.audioChunk
import app.cozybuddy.protocol.v1.audioFormat
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.responseAudioEnd
import app.cozybuddy.protocol.v1.responseAudioStart
import app.cozybuddy.protocol.v1.responseText
import app.cozybuddy.protocol.v1.transcript
import com.google.protobuf.ByteString
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class VoiceClientTest {

    private val link = FakeHubLink()
    private val capture = FakeAudioCapture()
    private val vad = ScriptedVad()
    private val player = FakeResponsePlayer()
    private val machine = AssistantStateMachine()
    private val alarm = FakeAlarmRinging()
    private val frameMs = CaptureFormat.FRAME_MS
    private val endFrames = (700 + frameMs - 1) / frameMs

    private val phase get() = machine.state.value.phase

    private class Harness(val client: VoiceClient, val notices: List<VoiceNotice>)

    private fun TestScope.harness(): Harness {
        val client = VoiceClient(link, capture, vad, player, machine, alarm, SequentialIds(), VoiceClientConfig(), backgroundScope)
        val notices = mutableListOf<VoiceNotice>()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { client.notices.toList(notices) }
        client.start()
        runCurrent()
        return Harness(client, notices)
    }

    private fun TestScope.feed(frames: Int, probability: Float) {
        vad.probability = probability
        repeat(frames) {
            capture.emit(1)
            runCurrent()
        }
    }

    private fun TestScope.speakAndFinish() {
        feed(5, 0.9f)
        feed(endFrames, 0f)
    }

    private suspend fun TestScope.receive(envelope: Envelope) {
        link.receive(envelope)
        runCurrent()
    }

    private fun audioStart(id: String, kind: StreamKind = StreamKind.STREAM_KIND_RESPONSE_DOWN) = envelope {
        responseAudioStart = responseAudioStart {
            streamId = id
            this.kind = kind
            format = audioFormat {
                encoding = AudioEncoding.AUDIO_ENCODING_PCM_S16LE
                sampleRateHz = 24_000
                channels = 1
            }
        }
    }

    private fun audioData(id: String, index: Int, kind: StreamKind = StreamKind.STREAM_KIND_RESPONSE_DOWN) = envelope {
        audioChunk = audioChunk {
            this.kind = kind
            streamId = id
            this.index = index
            data = ByteString.copyFrom(ByteArray(480))
        }
    }

    private fun audioEnd(id: String) = envelope { responseAudioEnd = responseAudioEnd { streamId = id } }

    private suspend fun TestScope.reachSpeaking(h: Harness) {
        link.connect()
        h.client.onTap()
        runCurrent()
        speakAndFinish()
        receive(audioStart("r1"))
    }

    @Test
    fun `nothing is sent before the tap`() = runTest {
        harness()
        link.connect()
        runCurrent()

        feed(40, 0.9f)

        assertTrue(link.sent.isEmpty())
        assertEquals(AssistantPhase.IDLE, phase)
    }

    @Test
    fun `tap while offline shows a notice and never listens`() = runTest {
        val h = harness()

        h.client.onTap()
        runCurrent()

        assertEquals(listOf(VoiceNotice.OFFLINE), h.notices)
        assertEquals(AssistantPhase.IDLE, phase)
        assertTrue(link.sent.isEmpty())
    }

    @Test
    fun `tap without a running microphone shows a notice`() = runTest {
        val h = harness()
        link.connect()
        capture.running.value = false

        h.client.onTap()
        runCurrent()

        assertEquals(listOf(VoiceNotice.MIC_UNAVAILABLE), h.notices)
        assertEquals(AssistantPhase.IDLE, phase)
    }

    @Test
    fun `full turn runs LISTENING THINKING SPEAKING IDLE with transcript and streamed text`() = runTest {
        val h = harness()
        link.connect()

        h.client.onTap()
        runCurrent()
        assertEquals(AssistantPhase.LISTENING, phase)
        assertEquals("living", link.sent.first().utteranceStart.areaId)

        feed(5, 0.9f)
        receive(envelope { transcript = transcript { streamId = "u1"; text = "불 켜줘"; final = false } })
        assertEquals("불 켜줘", h.client.conversation.value.transcript)

        feed(endFrames, 0f)
        assertEquals(AssistantPhase.THINKING, phase)
        assertEquals(Envelope.BodyCase.UTTERANCE_END, link.sent.last().bodyCase)

        receive(envelope { responseText = responseText { streamId = "u1"; delta = "거실 "; final = false } })
        receive(envelope { responseText = responseText { streamId = "u1"; delta = "불을 켰어요"; final = true } })
        assertEquals("거실 불을 켰어요", h.client.conversation.value.response)

        receive(audioStart("r1"))
        assertEquals(AssistantPhase.SPEAKING, phase)
        receive(audioData("r1", 0))
        receive(audioEnd("r1"))
        assertEquals(
            listOf("start:r1:STREAM_KIND_RESPONSE_DOWN:24000", "write:r1:0", "finish:r1"),
            player.calls,
        )
        assertEquals(AssistantPhase.SPEAKING, phase)

        player.finishPlayback("r1")
        runCurrent()
        assertEquals(AssistantPhase.IDLE, phase)
    }

    @Test
    fun `no speech timeout cancels with TIMEOUT and returns to IDLE`() = runTest {
        val h = harness()
        link.connect()
        h.client.onTap()
        runCurrent()

        feed(8_000 / frameMs, 0f)

        val cancel = link.sent.last()
        assertEquals(Envelope.BodyCase.UTTERANCE_CANCEL, cancel.bodyCase)
        assertEquals(CancelReason.CANCEL_REASON_TIMEOUT, cancel.cancelReason)
        assertEquals(AssistantPhase.IDLE, phase)
    }

    @Test
    fun `tap while speaking barges in on the old stream and starts a new utterance`() = runTest {
        val h = harness()
        reachSpeaking(h)
        assertEquals(AssistantPhase.SPEAKING, phase)

        h.client.onTap()
        runCurrent()

        assertEquals("stop", player.calls.last())
        val cancel = link.sent.ofCase(Envelope.BodyCase.UTTERANCE_CANCEL).single()
        assertEquals("u1", cancel.utteranceCancel.streamId)
        assertEquals(CancelReason.CANCEL_REASON_BARGE_IN, cancel.cancelReason)
        assertEquals(AssistantPhase.LISTENING, phase)
        assertEquals("u2", machine.state.value.utteranceId)
        assertEquals("u2", link.sent.ofCase(Envelope.BodyCase.UTTERANCE_START).last().utteranceStart.streamId)
    }

    @Test
    fun `late audio from the barged-in response is ignored`() = runTest {
        val h = harness()
        reachSpeaking(h)
        h.client.onTap()
        runCurrent()
        player.calls.clear()

        receive(audioData("r1", 1))
        receive(audioEnd("r1"))
        receive(audioStart("r1"))

        assertTrue(player.calls.none { it.startsWith("write") || it.startsWith("start") })
        assertEquals(AssistantPhase.LISTENING, phase)
    }

    @Test
    fun `alarm ringing disables tap to talk`() = runTest {
        val h = harness()
        link.connect()
        alarm.ringing.value = true
        runCurrent()

        h.client.onTap()
        runCurrent()

        assertEquals(AssistantPhase.IDLE, phase)
        assertTrue(link.sent.isEmpty())
        assertTrue(h.notices.isEmpty())

        alarm.ringing.value = false
        runCurrent()
        h.client.onTap()
        runCurrent()
        assertEquals(AssistantPhase.LISTENING, phase)
    }

    @Test
    fun `alarm ringing cancels an active utterance and silences the response`() = runTest {
        val h = harness()
        link.connect()
        h.client.onTap()
        runCurrent()
        feed(5, 0.9f)
        val sentBefore = link.sent.size

        alarm.ringing.value = true
        runCurrent()
        feed(5, 0.9f)

        assertEquals(AssistantPhase.IDLE, phase)
        assertEquals(sentBefore + 1, link.sent.size)
        assertEquals(CancelReason.CANCEL_REASON_USER, link.sent.last().cancelReason)
        assertTrue(player.calls.contains("stop"))
    }

    @Test
    fun `disconnect while listening drops to IDLE and stops sending audio`() = runTest {
        val h = harness()
        link.connect()
        h.client.onTap()
        runCurrent()
        feed(5, 0.9f)
        val sentBefore = link.sent.size

        link.state.value = ConnectionState.Disconnected
        runCurrent()
        feed(5, 0.9f)

        assertEquals(AssistantPhase.IDLE, phase)
        assertEquals(sentBefore, link.sent.size)
        assertEquals(listOf(VoiceNotice.OFFLINE), h.notices)
    }

    @Test
    fun `disconnect while speaking stops playback`() = runTest {
        val h = harness()
        reachSpeaking(h)

        link.state.value = ConnectionState.Disconnected
        runCurrent()

        assertEquals(AssistantPhase.IDLE, phase)
        assertEquals("stop", player.calls.last())
    }

    @Test
    fun `no response within the timeout ends THINKING with an error`() = runTest {
        val h = harness()
        link.connect()
        h.client.onTap()
        runCurrent()
        speakAndFinish()
        assertEquals(AssistantPhase.THINKING, phase)

        advanceTimeBy(VoiceClientConfig().thinkingTimeoutMs + 1)
        runCurrent()

        assertEquals(AssistantPhase.IDLE, phase)
        assertEquals(listOf(VoiceNotice.ERROR), h.notices)
    }

    @Test
    fun `server error for the current stream ends the turn`() = runTest {
        val h = harness()
        link.connect()
        h.client.onTap()
        runCurrent()
        speakAndFinish()

        receive(
            envelope {
                error = app.cozybuddy.protocol.v1.errorMessage { streamId = "u1" }
            },
        )

        assertEquals(AssistantPhase.IDLE, phase)
        assertEquals(listOf(VoiceNotice.ERROR), h.notices)
    }

    @Test
    fun `broadcast audio plays only while the assistant is idle and leaves the state machine alone`() = runTest {
        val h = harness()
        link.connect()
        runCurrent()

        receive(audioStart("b1", StreamKind.STREAM_KIND_BROADCAST_DOWN))
        receive(audioData("b1", 0, StreamKind.STREAM_KIND_BROADCAST_DOWN))
        assertEquals(AssistantPhase.IDLE, phase)
        assertEquals(listOf("start:b1:STREAM_KIND_BROADCAST_DOWN:24000", "write:b1:0"), player.calls)

        // 대화가 시작되면 방송은 즉시 끊긴다.
        h.client.onTap()
        runCurrent()
        assertEquals("stop", player.calls.last())
        assertEquals(AssistantPhase.LISTENING, phase)
    }

    @Test
    fun `broadcast is skipped while a conversation is in progress`() = runTest {
        val h = harness()
        link.connect()
        h.client.onTap()
        runCurrent()

        receive(audioStart("b1", StreamKind.STREAM_KIND_BROADCAST_DOWN))

        assertTrue(player.calls.isEmpty())
    }

    @Test
    fun `wake word starts an utterance with WAKE_WORD source and a one second pre-roll`() = runTest {
        val h = harness()
        link.connect()
        repeat(40) { capture.emit(1) }
        runCurrent()

        h.client.onWakeWord()
        runCurrent()

        val start = link.sent.ofCase(Envelope.BodyCase.UTTERANCE_START).single().utteranceStart
        assertEquals(WakeSource.WAKE_SOURCE_WAKE_WORD, start.source)
        assertEquals(1_000, start.preRollMs)
        assertEquals(AssistantPhase.LISTENING, phase)
    }

    @Test
    fun `tap keeps the 300 ms pre-roll`() = runTest {
        val h = harness()
        link.connect()
        repeat(40) { capture.emit(1) }
        runCurrent()

        h.client.onTap()
        runCurrent()

        val start = link.sent.ofCase(Envelope.BodyCase.UTTERANCE_START).single().utteranceStart
        assertEquals(WakeSource.WAKE_SOURCE_TAP, start.source)
        assertEquals(300, start.preRollMs)
    }

    @Test
    fun `wake word while offline or without microphone is ignored silently`() = runTest {
        val h = harness()
        h.client.onWakeWord()
        runCurrent()
        link.connect()
        capture.running.value = false
        h.client.onWakeWord()
        runCurrent()

        assertTrue(link.sent.isEmpty())
        assertTrue(h.notices.isEmpty())
        assertEquals(AssistantPhase.IDLE, phase)
    }

    @Test
    fun `wake word opens listening while the alarm rings`() = runTest {
        val h = harness()
        link.connect()
        alarm.ringing.value = true
        runCurrent()

        h.client.onWakeWord()
        runCurrent()

        assertEquals(AssistantPhase.LISTENING, phase)
        assertTrue(machine.state.value.alarmRinging)
        assertEquals(1, link.sent.ofCase(Envelope.BodyCase.UTTERANCE_START).size)
    }

    @Test
    fun `wake word while speaking barges in with the wake word source`() = runTest {
        val h = harness()
        reachSpeaking(h)

        h.client.onWakeWord()
        runCurrent()

        assertEquals("stop", player.calls.last())
        assertEquals(CancelReason.CANCEL_REASON_BARGE_IN, link.sent.ofCase(Envelope.BodyCase.UTTERANCE_CANCEL).single().cancelReason)
        val start = link.sent.ofCase(Envelope.BodyCase.UTTERANCE_START).last().utteranceStart
        assertEquals(WakeSource.WAKE_SOURCE_WAKE_WORD, start.source)
        assertEquals("u2", start.streamId)
    }

    @Test
    fun `wake word during an active utterance is ignored`() = runTest {
        val h = harness()
        link.connect()
        h.client.onTap()
        runCurrent()

        h.client.onWakeWord()
        runCurrent()

        assertEquals(1, link.sent.ofCase(Envelope.BodyCase.UTTERANCE_START).size)
        assertEquals("u1", machine.state.value.utteranceId)
    }
}
