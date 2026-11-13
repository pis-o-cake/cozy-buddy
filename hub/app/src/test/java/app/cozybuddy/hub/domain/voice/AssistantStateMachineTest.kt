package app.cozybuddy.hub.domain.voice

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class AssistantStateMachineTest {

    private val machine = AssistantStateMachine()
    private val phase get() = machine.state.value.phase

    private fun reachSpeaking(utterance: String = "u1", response: String = "r1") {
        machine.onTap(utterance)
        machine.onUtteranceEnded(utterance)
        machine.onResponseAudioStart(response)
    }

    @Test
    fun `happy path walks IDLE LISTENING THINKING SPEAKING IDLE`() {
        assertEquals(AssistantPhase.IDLE, phase)

        assertEquals(TapResult.Started, machine.onTap("u1"))
        assertEquals(AssistantPhase.LISTENING, phase)
        assertEquals("u1", machine.state.value.utteranceId)

        assertTrue(machine.onUtteranceEnded("u1"))
        assertEquals(AssistantPhase.THINKING, phase)

        assertTrue(machine.onResponseAudioStart("r1"))
        assertEquals(AssistantPhase.SPEAKING, phase)
        assertEquals("r1", machine.state.value.responseId)

        assertTrue(machine.onPlaybackDrained("r1"))
        assertEquals(AssistantSnapshot(), machine.state.value)
    }

    @Test
    fun `no speech timeout returns LISTENING to IDLE`() {
        machine.onTap("u1")

        assertTrue(machine.onNoSpeech("u1"))

        assertEquals(AssistantPhase.IDLE, phase)
        assertNull(machine.state.value.utteranceId)
    }

    @Test
    fun `events for a different stream are ignored`() {
        machine.onTap("u1")

        assertFalse(machine.onUtteranceEnded("other"))
        assertFalse(machine.onNoSpeech("other"))
        assertEquals(AssistantPhase.LISTENING, phase)
    }

    @Test
    fun `response audio is only accepted while THINKING`() {
        assertFalse(machine.onResponseAudioStart("r1"))
        machine.onTap("u1")
        assertFalse(machine.onResponseAudioStart("r1"))
        assertEquals(AssistantPhase.LISTENING, phase)
    }

    @Test
    fun `drain of an old response does not end the current one`() {
        reachSpeaking(response = "r2")

        assertFalse(machine.onPlaybackDrained("r1"))
        assertEquals(AssistantPhase.SPEAKING, phase)
    }

    @Test
    fun `tap while SPEAKING is a barge-in that starts a new utterance`() {
        reachSpeaking("u1", "r1")

        val result = machine.onTap("u2")

        assertEquals(TapResult.BargeIn("u1"), result)
        assertEquals(AssistantPhase.LISTENING, phase)
        assertEquals("u2", machine.state.value.utteranceId)
        assertNull(machine.state.value.responseId)
    }

    @Test
    fun `tap while LISTENING or THINKING is ignored`() {
        machine.onTap("u1")
        assertEquals(TapResult.Ignored(TapIgnoredReason.BUSY), machine.onTap("u2"))
        machine.onUtteranceEnded("u1")
        assertEquals(TapResult.Ignored(TapIgnoredReason.BUSY), machine.onTap("u2"))
        assertEquals("u1", machine.state.value.utteranceId)
    }

    @Test
    fun `alarm ringing locks tap to talk`() {
        machine.onAlarmRinging(true)

        assertEquals(TapResult.Ignored(TapIgnoredReason.ALARM_RINGING), machine.onTap("u1"))
        assertEquals(AssistantPhase.IDLE, phase)

        machine.onAlarmRinging(false)
        assertEquals(TapResult.Started, machine.onTap("u1"))
    }

    @Test
    fun `alarm ringing interrupts an active conversation`() {
        reachSpeaking()

        machine.onAlarmRinging(true)

        assertEquals(AssistantPhase.IDLE, phase)
        assertTrue(machine.state.value.alarmRinging)
        assertNull(machine.state.value.responseId)
    }

    @Test
    fun `error and disconnect return any state to IDLE`() {
        machine.onTap("u1")
        assertTrue(machine.onError())
        assertEquals(AssistantPhase.IDLE, phase)

        reachSpeaking("u2", "r2")
        assertTrue(machine.onDisconnected())
        assertEquals(AssistantSnapshot(), machine.state.value)

        assertFalse(machine.onDisconnected())
    }

    @Test
    fun `wake word starts listening from IDLE`() {
        assertEquals(TapResult.Started, machine.onWakeWord("u1"))
        assertEquals(AssistantPhase.LISTENING, phase)
        assertEquals("u1", machine.state.value.utteranceId)
    }

    @Test
    fun `wake word starts listening while the alarm rings but tap stays blocked`() {
        machine.onAlarmRinging(true)

        assertEquals(TapResult.Ignored(TapIgnoredReason.ALARM_RINGING), machine.onTap("u1"))
        assertEquals(TapResult.Started, machine.onWakeWord("u2"))
        assertEquals(AssistantPhase.LISTENING, phase)
        assertTrue(machine.state.value.alarmRinging)
    }

    @Test
    fun `wake word is ignored while LISTENING or THINKING`() {
        machine.onWakeWord("u1")
        assertEquals(TapResult.Ignored(TapIgnoredReason.BUSY), machine.onWakeWord("u2"))
        machine.onUtteranceEnded("u1")
        assertEquals(TapResult.Ignored(TapIgnoredReason.BUSY), machine.onWakeWord("u2"))
        assertEquals("u1", machine.state.value.utteranceId)
    }

    @Test
    fun `wake word while speaking barges in`() {
        reachSpeaking("u1", "r1")

        assertEquals(TapResult.BargeIn("u1"), machine.onWakeWord("u2"))
        assertEquals("u2", machine.state.value.utteranceId)
        assertEquals(AssistantPhase.LISTENING, phase)
    }
}
