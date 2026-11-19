package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.protocol.v1.AudioEncoding
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.StreamKind
import app.cozybuddy.protocol.v1.audioChunk
import app.cozybuddy.protocol.v1.audioFormat
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.responseAudioStart
import com.google.protobuf.ByteString
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class VoiceClientReminderTest {

    private val link = FakeHubLink()
    private val player = FakeResponsePlayer()
    private val machine = AssistantStateMachine()
    private val alarm = FakeAlarmRinging()

    private class Harness(val client: VoiceClient, val speech: List<ReminderSpeech>)

    private fun TestScope.harness(): Harness {
        val client = VoiceClient(
            link,
            FakeAudioCapture(),
            ScriptedVad(),
            player,
            machine,
            alarm,
            SequentialIds(),
            VoiceClientConfig(),
            backgroundScope,
        )
        val speech = mutableListOf<ReminderSpeech>()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { client.reminderSpeech.toList(speech) }
        client.start()
        runCurrent()
        return Harness(client, speech)
    }

    private suspend fun TestScope.receive(envelope: Envelope) {
        link.receive(envelope)
        runCurrent()
    }

    private fun audioStart(id: String) = envelope {
        responseAudioStart = responseAudioStart {
            streamId = id
            kind = StreamKind.STREAM_KIND_RESPONSE_DOWN
            format = audioFormat {
                encoding = AudioEncoding.AUDIO_ENCODING_PCM_S16LE
                sampleRateHz = 24_000
                channels = 1
            }
        }
    }

    private fun audioData(id: String, index: Int, last: Boolean = false) = envelope {
        audioChunk = audioChunk {
            kind = StreamKind.STREAM_KIND_RESPONSE_DOWN
            streamId = id
            this.index = index
            data = ByteString.copyFrom(ByteArray(480))
            this.last = last
        }
    }

    private fun TestScope.startRinging() {
        alarm.ringing.value = true
        runCurrent()
        // 울림이 시작되면 진행 중이던 재생을 끊는 stop 이 한 번 기록된다. 이후 호출만 보려고 비운다.
        player.calls.clear()
    }

    private val sentReminders get() = link.sent.ofCase(Envelope.BodyCase.REMINDER_FIRED).map { it.reminderFired }

    @Test
    fun `online fire sends ReminderFired with the alarm id and label`() = runTest {
        val h = harness()
        link.connect()
        startRinging()

        h.client.onReminderFired("m-1", "병원 가기")
        runCurrent()

        val sent = sentReminders.single()
        assertEquals("m-1", sent.alarmId)
        assertEquals("병원 가기", sent.label)
    }

    @Test
    fun `offline fire sends nothing and later audio is not treated as reminder speech`() = runTest {
        val h = harness()
        startRinging()

        h.client.onReminderFired("m-1", "병원 가기")
        runCurrent()
        link.connect()
        receive(audioStart("reminder-m-1-r"))

        assertTrue(sentReminders.isEmpty())
        assertTrue(player.calls.isEmpty())
        assertTrue(h.speech.isEmpty())
    }

    @Test
    fun `reminder speech starts playback while idle and reports started then drained`() = runTest {
        val h = harness()
        link.connect()
        startRinging()
        h.client.onReminderFired("m-1", "병원 가기")
        runCurrent()

        receive(audioStart("reminder-m-1-r"))
        receive(audioData("reminder-m-1-r", 0))
        receive(audioData("reminder-m-1-r", 1, last = true))

        assertEquals(AssistantPhase.IDLE, machine.state.value.phase)
        assertEquals(
            listOf(
                "start:reminder-m-1-r:STREAM_KIND_RESPONSE_DOWN:24000",
                "write:reminder-m-1-r:0",
                "write:reminder-m-1-r:1",
                "finish:reminder-m-1-r",
            ),
            player.calls,
        )
        assertEquals(listOf<ReminderSpeech>(ReminderSpeech.Started("m-1")), h.speech)

        player.finishPlayback("reminder-m-1-r")
        runCurrent()

        assertEquals(listOf(ReminderSpeech.Started("m-1"), ReminderSpeech.Drained("m-1")), h.speech)
    }

    @Test
    fun `response audio that was not requested is ignored while idle`() = runTest {
        val h = harness()
        link.connect()
        startRinging()

        receive(audioStart("reminder-x-r"))

        assertTrue(player.calls.isEmpty())
        assertTrue(h.speech.isEmpty())
    }

    @Test
    fun `speech from another stream family is not accepted as the reminder`() = runTest {
        val h = harness()
        link.connect()
        startRinging()
        h.client.onReminderFired("m-1", "병원 가기")
        runCurrent()

        receive(audioStart("u7-r"))

        assertTrue(player.calls.isEmpty())
        assertTrue(h.speech.isEmpty())
    }

    @Test
    fun `stopping the ring cuts the reminder speech`() = runTest {
        val h = harness()
        link.connect()
        startRinging()
        h.client.onReminderFired("m-1", "병원 가기")
        runCurrent()
        receive(audioStart("reminder-m-1-r"))

        alarm.ringing.value = false
        runCurrent()

        assertEquals("stop", player.calls.last())
    }
}
