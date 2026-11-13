package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.feature.ambient.ClockDisplayConfig
import io.mockk.mockk
import io.mockk.verify
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.test.StandardTestDispatcher
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
class AlarmRingViewModelTest {

    private val zone = ZoneId.of("Asia/Seoul")
    private val holder = RingingStateHolder()
    private val control = mockk<AlarmRingControl>(relaxed = true)
    private val timeSource = object : TimeSource {
        override fun now(): ZonedDateTime = ZonedDateTime.of(2026, 11, 13, 7, 0, 0, 0, zone)
    }
    private val displayConfig = object : ClockDisplayConfig {
        override fun is24Hour() = true
        override fun locale(): Locale = Locale.KOREA
        override fun datePattern() = "M월 d일"
    }

    @BeforeEach
    fun setUp() = Dispatchers.setMain(StandardTestDispatcher())

    @AfterEach
    fun tearDown() = Dispatchers.resetMain()

    @Test
    fun `overlay follows the ringing state holder`() = runTest {
        val viewModel = AlarmRingViewModel(holder, control, timeSource, displayConfig)
        val job = viewModel.uiState.onEach { }.launchIn(backgroundScope)
        runCurrent()
        assertFalse(viewModel.uiState.value.visible)

        val startedAt = timeSource.now().toInstant().toEpochMilli()
        holder.start(RingingState.Ringing("a", AlarmKind.ALARM, "기상", startedAt))
        runCurrent()

        val ringing = viewModel.uiState.value
        assertTrue(ringing.visible)
        assertEquals("기상", ringing.label)
        assertEquals("07:00", ringing.timeText)
        assertEquals(9, ringing.snoozeMinutes)

        holder.clear()
        runCurrent()
        assertFalse(viewModel.uiState.value.visible)
        job.cancel()
    }

    @Test
    fun `stop and snooze are delegated to the control`() {
        val viewModel = AlarmRingViewModel(holder, control, timeSource, displayConfig)

        viewModel.onStop()
        viewModel.onSnooze()

        verify(exactly = 1) { control.stop() }
        verify(exactly = 1) { control.snooze() }
    }
}
