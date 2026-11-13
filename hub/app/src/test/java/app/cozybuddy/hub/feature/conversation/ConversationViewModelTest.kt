package app.cozybuddy.hub.feature.conversation

import app.cozybuddy.hub.domain.voice.AssistantPhase
import app.cozybuddy.hub.domain.voice.AssistantSnapshot
import app.cozybuddy.hub.domain.voice.ConversationText
import app.cozybuddy.hub.domain.voice.VoiceClient
import app.cozybuddy.hub.domain.voice.VoiceNotice
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ConversationViewModelTest {

    private val state = MutableStateFlow(AssistantSnapshot())
    private val text = MutableStateFlow(ConversationText())
    private val level = MutableStateFlow(0f)
    private val notices = MutableSharedFlow<VoiceNotice>(extraBufferCapacity = 4)
    private val client = mockk<VoiceClient>(relaxed = true) {
        every { this@mockk.state } returns this@ConversationViewModelTest.state
        every { conversation } returns text
        every { micLevel } returns level
        every { this@mockk.notices } returns this@ConversationViewModelTest.notices
    }

    @BeforeEach
    fun setUp() = Dispatchers.setMain(StandardTestDispatcher())

    @AfterEach
    fun tearDown() = Dispatchers.resetMain()

    private fun phase(phase: AssistantPhase) {
        state.value = AssistantSnapshot(phase = phase, utteranceId = if (phase == AssistantPhase.IDLE) null else "u1")
    }

    @Test
    fun `overlay is hidden until a conversation starts`() = runTest {
        val viewModel = ConversationViewModel(client)
        runCurrent()

        assertFalse(viewModel.uiState.value.visible)

        phase(AssistantPhase.LISTENING)
        runCurrent()
        assertTrue(viewModel.uiState.value.visible)
        assertEquals(AssistantPhase.LISTENING, viewModel.uiState.value.phase)
    }

    @Test
    fun `overlay dismisses 4 seconds after returning to IDLE`() = runTest {
        val viewModel = ConversationViewModel(client)
        phase(AssistantPhase.SPEAKING)
        runCurrent()
        text.value = ConversationText(response = "안녕하세요")
        phase(AssistantPhase.IDLE)
        runCurrent()

        advanceTimeBy(ConversationConfig.AUTO_DISMISS_MS - 100)
        runCurrent()
        assertTrue(viewModel.uiState.value.visible)
        assertEquals("안녕하세요", viewModel.uiState.value.response)

        advanceTimeBy(200)
        runCurrent()
        assertFalse(viewModel.uiState.value.visible)
    }

    @Test
    fun `a new conversation cancels the pending dismissal`() = runTest {
        val viewModel = ConversationViewModel(client)
        phase(AssistantPhase.THINKING)
        runCurrent()
        phase(AssistantPhase.IDLE)
        runCurrent()
        advanceTimeBy(3_000)

        phase(AssistantPhase.LISTENING)
        runCurrent()
        advanceTimeBy(5_000)
        runCurrent()

        assertTrue(viewModel.uiState.value.visible)
    }

    @Test
    fun `notice shows the overlay and disappears after the same delay`() = runTest {
        val viewModel = ConversationViewModel(client)
        runCurrent()

        notices.tryEmit(VoiceNotice.OFFLINE)
        runCurrent()
        assertTrue(viewModel.uiState.value.visible)
        assertEquals(ConversationNotice.OFFLINE, viewModel.uiState.value.notice)

        advanceTimeBy(ConversationConfig.AUTO_DISMISS_MS + 100)
        runCurrent()
        assertFalse(viewModel.uiState.value.visible)
        assertEquals(ConversationNotice.NONE, viewModel.uiState.value.notice)
    }

    @Test
    fun `mic level history is kept only while listening and bounded`() = runTest {
        val viewModel = ConversationViewModel(client)
        phase(AssistantPhase.LISTENING)
        runCurrent()

        repeat(ConversationConfig.LEVEL_HISTORY + 10) {
            level.value = (it + 1) / 100f
            runCurrent()
        }
        assertEquals(ConversationConfig.LEVEL_HISTORY, viewModel.uiState.value.levels.size)

        phase(AssistantPhase.THINKING)
        runCurrent()
        assertTrue(viewModel.uiState.value.levels.isEmpty())
    }

    @Test
    fun `mic button is disabled while an alarm rings`() = runTest {
        val viewModel = ConversationViewModel(client)
        runCurrent()
        assertTrue(viewModel.uiState.value.micEnabled)

        state.value = AssistantSnapshot(alarmRinging = true)
        runCurrent()

        assertFalse(viewModel.uiState.value.micEnabled)
    }

    @Test
    fun `overlay tap barges in only while speaking and dismisses while lingering`() = runTest {
        val viewModel = ConversationViewModel(client)
        phase(AssistantPhase.THINKING)
        runCurrent()
        viewModel.onOverlayTap()
        verify(exactly = 0) { client.onTap() }

        phase(AssistantPhase.SPEAKING)
        runCurrent()
        viewModel.onOverlayTap()
        verify(exactly = 1) { client.onTap() }

        phase(AssistantPhase.IDLE)
        runCurrent()
        viewModel.onOverlayTap()
        runCurrent()
        assertFalse(viewModel.uiState.value.visible)
        verify(exactly = 1) { client.onTap() }
    }

    @Test
    fun `mic button taps are forwarded`() = runTest {
        val viewModel = ConversationViewModel(client)

        viewModel.onMicTap()

        verify(exactly = 1) { client.onTap() }
    }
}
