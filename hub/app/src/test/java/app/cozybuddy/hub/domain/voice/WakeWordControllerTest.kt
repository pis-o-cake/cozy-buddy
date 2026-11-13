package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.wakeword.WakeEvent
import app.cozybuddy.hub.core.audio.wakeword.WakeSensitivity
import app.cozybuddy.hub.core.audio.wakeword.WakeSpeakingPolicy
import app.cozybuddy.hub.core.audio.wakeword.WakeWordConfig
import app.cozybuddy.hub.core.audio.wakeword.WakeWordEngine
import app.cozybuddy.hub.core.audio.wakeword.WakeWordFailure
import app.cozybuddy.hub.core.audio.wakeword.WakeWordPrefs
import app.cozybuddy.hub.core.audio.wakeword.WakeWordSettings
import app.cozybuddy.hub.core.audio.wakeword.WakeWordStatus
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

private class FakeWakeWordEngine(var ready: Boolean = true) : WakeWordEngine {
    override val status = MutableStateFlow<WakeWordStatus>(WakeWordStatus.Idle)
    override val events = MutableSharedFlow<WakeEvent>(extraBufferCapacity = 4)
    var processed = 0
    var resets = 0
    var released = false
    val thresholds = mutableListOf<Float>()

    override suspend fun initialize(): Boolean {
        status.value = if (ready) WakeWordStatus.Ready else WakeWordStatus.Failed(WakeWordFailure.MODEL_MISSING)
        return ready
    }

    override suspend fun process(frame: ShortArray): Float = 0f.also { processed++ }

    override fun setThreshold(threshold: Float) {
        thresholds += threshold
    }

    override fun reset() {
        resets++
    }

    override fun release() {
        released = true
    }
}

private class FakeWakeWordSettings(initial: WakeWordPrefs = WakeWordPrefs()) : WakeWordSettings {
    override val prefs = MutableStateFlow(initial)

    override suspend fun setEnabled(enabled: Boolean) {
        prefs.value = prefs.value.copy(enabled = enabled)
    }

    override suspend fun setSensitivity(sensitivity: Float) {
        prefs.value = prefs.value.copy(sensitivity = sensitivity)
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class WakeWordControllerTest {

    private val engine = FakeWakeWordEngine()
    private val capture = FakeAudioCapture()
    private val settings = FakeWakeWordSettings()
    private val machine = AssistantStateMachine()
    private val voiceClient = mockk<VoiceClient>(relaxed = true)

    private fun TestScope.controller(config: WakeWordConfig = WakeWordConfig()): WakeWordController {
        val controller = WakeWordController(engine, capture, settings, machine, voiceClient, config, backgroundScope)
        controller.start()
        runCurrent()
        return controller
    }

    private fun TestScope.frames(count: Int) = repeat(count) {
        capture.emit(1)
        runCurrent()
    }

    private fun reachSpeaking() {
        machine.onWakeWord("u1")
        machine.onUtteranceEnded("u1")
        machine.onResponseAudioStart("r1")
    }

    @Test
    fun `frames reach the engine while IDLE with the configured threshold`() = runTest {
        controller()

        frames(3)

        assertEquals(3, engine.processed)
        assertEquals(WakeSensitivity.toThreshold(WakeSensitivity.DEFAULT), engine.thresholds.first(), 1e-6f)
    }

    @Test
    fun `detection event opens an utterance through the voice client`() = runTest {
        controller()

        engine.events.emit(WakeEvent(0.9f, 1_000))
        runCurrent()

        verify(exactly = 1) { voiceClient.onWakeWord() }
    }

    @Test
    fun `frames are not fed while LISTENING and THINKING and the engine is reset on return to IDLE`() = runTest {
        controller()
        frames(2)
        val resetsBefore = engine.resets

        machine.onWakeWord("u1")
        frames(3)
        machine.onUtteranceEnded("u1")
        frames(3)
        assertEquals(2, engine.processed)

        machine.onError()
        frames(1)

        assertEquals(3, engine.processed)
        assertTrue(engine.resets > resetsBefore)
    }

    @Test
    fun `while speaking the threshold is raised by default`() = runTest {
        controller()
        reachSpeaking()

        frames(2)

        assertEquals(2, engine.processed)
        val base = WakeSensitivity.toThreshold(WakeSensitivity.DEFAULT)
        assertEquals(base + 0.25f, engine.thresholds.last(), 1e-6f)
    }

    @Test
    fun `leaving SPEAKING restores the base threshold`() = runTest {
        controller()
        reachSpeaking()
        frames(1)

        machine.onPlaybackDrained("r1")
        frames(1)

        assertEquals(WakeSensitivity.toThreshold(WakeSensitivity.DEFAULT), engine.thresholds.last(), 1e-6f)
    }

    @Test
    fun `gate off policy feeds nothing while speaking`() = runTest {
        controller(WakeWordConfig(speakingPolicy = WakeSpeakingPolicy.GATE_OFF))
        reachSpeaking()

        frames(5)

        assertEquals(0, engine.processed)
    }

    @Test
    fun `detection stays on while the alarm rings`() = runTest {
        controller()
        machine.onAlarmRinging(true)

        frames(2)

        assertEquals(2, engine.processed)
    }

    @Test
    fun `disabled setting keeps the engine off and enabling starts it`() = runTest {
        settings.prefs.value = WakeWordPrefs(enabled = false)
        controller()

        frames(3)
        assertEquals(0, engine.processed)

        settings.setEnabled(true)
        runCurrent()
        frames(2)

        assertEquals(2, engine.processed)
    }

    @Test
    fun `sensitivity change re-applies the threshold`() = runTest {
        controller()

        settings.setSensitivity(1f)
        runCurrent()
        frames(1)

        assertEquals(WakeSensitivity.toThreshold(1f), engine.thresholds.last(), 1e-6f)
    }

    @Test
    fun `stopped microphone stops feeding`() = runTest {
        controller()
        frames(1)

        capture.running.value = false
        runCurrent()
        capture.emit(1)
        runCurrent()

        assertEquals(1, engine.processed)
    }

    @Test
    fun `unavailable engine never receives frames`() = runTest {
        engine.ready = false
        controller()

        frames(3)

        assertEquals(0, engine.processed)
    }

    @Test
    fun `stop releases the engine`() = runTest {
        val controller = controller()

        controller.stop()

        assertTrue(engine.released)
    }
}
