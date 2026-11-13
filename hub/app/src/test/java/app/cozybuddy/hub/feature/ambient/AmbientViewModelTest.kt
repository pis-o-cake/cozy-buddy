package app.cozybuddy.hub.feature.ambient

import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.hub.core.network.HubInfo
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.weather.WeatherRepository
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.weatherDaily
import app.cozybuddy.protocol.v1.weatherSnapshot
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AmbientViewModelTest {

    private val zone = ZoneId.of("Asia/Seoul")
    private val scheduler = TestCoroutineScheduler()
    private val link = FakeHubLink()
    private val weatherScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var weather: WeatherRepository

    @BeforeEach
    fun setUp() {
        Dispatchers.setMain(StandardTestDispatcher(scheduler))
        weather = WeatherRepository(link, weatherScope)
        weather.start()
    }

    @AfterEach
    fun tearDown() {
        weatherScope.cancel()
        Dispatchers.resetMain()
    }

    private fun at(hour: Int, minute: Int, second: Int = 0): ZonedDateTime =
        ZonedDateTime.of(2026, 11, 12, hour, minute, second, 0, zone)

    private fun viewModel(
        start: ZonedDateTime,
        is24Hour: Boolean = true,
        locale: Locale = Locale.KOREAN,
        pattern: String = "M월 d일 EEEE",
    ) = AmbientViewModel(
        timeSource = SchedulerTimeSource(scheduler, start),
        displayConfig = FakeDisplayConfig(is24Hour, locale, pattern),
        link = link,
        weather = weather,
    )

    @Test
    fun `fixed time produces digits and Korean date`() {
        val state = buildAmbientState(at(13, 5), true, Locale.KOREAN, "M월 d일 EEEE")

        assertEquals(listOf(1, 3, 0, 5), state.digits)
        assertEquals("11월 12일 목요일", state.dateText)
    }

    @Test
    fun `12 hour format wraps afternoon and midnight`() {
        assertEquals(
            listOf(0, 1, 0, 5),
            buildAmbientState(at(13, 5), false, Locale.KOREAN, "M월 d일 EEEE").digits,
        )
        assertEquals(
            listOf(1, 2, 0, 0),
            buildAmbientState(at(0, 0), false, Locale.KOREAN, "M월 d일 EEEE").digits,
        )
    }

    @Test
    fun `date follows locale pattern`() {
        val state = buildAmbientState(at(9, 0), true, Locale.ENGLISH, "EEEE, MMMM d")

        assertEquals("Thursday, November 12", state.dateText)
    }

    @Test
    fun `dim switches on at 22 00 and off at 07 00`() {
        assertEquals(DimLevel.NONE, dimLevelAt(LocalTime.of(21, 59)))
        assertEquals(DimLevel.NIGHT, dimLevelAt(LocalTime.of(22, 0)))
        assertEquals(DimLevel.NIGHT, dimLevelAt(LocalTime.MIDNIGHT))
        assertEquals(DimLevel.NIGHT, dimLevelAt(LocalTime.of(6, 59)))
        assertEquals(DimLevel.NONE, dimLevelAt(LocalTime.of(7, 0)))
        assertEquals(DimLevel.NONE, dimLevelAt(LocalTime.NOON))
    }

    @Test
    fun `pixel shift advances every minute and cycles`() {
        val cycle = AmbientConfig.PIXEL_SHIFT_CYCLE
        val start = at(12, 0)
        val shifts = (0 until cycle.size + 1).map { pixelShiftAt(start.plusMinutes(it.toLong())) }

        assertEquals(cycle.size, shifts.take(cycle.size).toSet().size)
        assertEquals(shifts.first(), shifts.last())
        assertNotEquals(shifts[0], shifts[1])
    }

    @Test
    fun `pixel shift stays within the same minute`() {
        assertEquals(pixelShiftAt(at(12, 0, 0)), pixelShiftAt(at(12, 0, 59)))
    }

    @Test
    fun `next tick is aligned to the minute boundary`() {
        assertEquals(60_000L, millisUntilNextMinute(0L))
        assertEquals(1_000L, millisUntilNextMinute(59_000L))
        assertEquals(30_000L, millisUntilNextMinute(at(12, 0, 30).toInstant().toEpochMilli()))
    }

    @Test
    fun `view model ticks at minute boundary and crosses into night`() = runTest(scheduler) {
        val vm = viewModel(at(21, 59, 30))
        val job = backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()

        assertEquals(listOf(2, 1, 5, 9), vm.uiState.value.digits)
        assertEquals(DimLevel.NONE, vm.uiState.value.dim)

        advanceTimeBy(29_999)
        runCurrent()
        assertEquals(listOf(2, 1, 5, 9), vm.uiState.value.digits)

        advanceTimeBy(1)
        runCurrent()
        assertEquals(listOf(2, 2, 0, 0), vm.uiState.value.digits)
        assertEquals(DimLevel.NIGHT, vm.uiState.value.dim)
        job.cancel()
    }

    @Test
    fun `weather snapshot appears in ui state and stale weather disappears on the next tick`() = runTest(scheduler) {
        val start = at(12, 0)
        val vm = viewModel(start)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()
        assertNull(vm.uiState.value.weather)

        link.receive(
            envelope {
                weatherSnapshot = weatherSnapshot {
                    observedEpochMs = start.toInstant().toEpochMilli()
                    temperatureC = 12.4
                    weatherCode = 0
                    today = weatherDaily { minC = 3.0; maxC = 15.2 }
                }
            },
        )
        runCurrent()
        assertEquals(AmbientWeather(WeatherCondition.CLEAR, 12, 15, 3), vm.uiState.value.weather)

        advanceTimeBy(WEATHER_STALE_MS + 120_000)
        runCurrent()
        assertNull(vm.uiState.value.weather)
    }

    @Test
    fun `offline indicator appears only after the grace period`() = runTest(scheduler) {
        val vm = viewModel(at(12, 0))
        backgroundScope.launch { vm.connectionIndicator.collect {} }
        runCurrent()

        link.state.value = ConnectionState.Connecting
        advanceTimeBy(9_999)
        runCurrent()
        assertEquals(ConnectionIndicator.NONE, vm.connectionIndicator.value)

        advanceTimeBy(1)
        runCurrent()
        assertEquals(ConnectionIndicator.OFFLINE, vm.connectionIndicator.value)
    }

    @Test
    fun `reconnect attempts do not restart the grace period`() = runTest(scheduler) {
        val vm = viewModel(at(12, 0))
        backgroundScope.launch { vm.connectionIndicator.collect {} }
        runCurrent()

        link.state.value = ConnectionState.Disconnected
        advanceTimeBy(6_000)
        link.state.value = ConnectionState.Connecting
        advanceTimeBy(4_000)
        runCurrent()

        assertEquals(ConnectionIndicator.OFFLINE, vm.connectionIndicator.value)
    }

    @Test
    fun `connection clears the indicator and auth failure shows immediately`() = runTest(scheduler) {
        val vm = viewModel(at(12, 0))
        backgroundScope.launch { vm.connectionIndicator.collect {} }
        runCurrent()

        link.state.value = ConnectionState.AuthFailed
        runCurrent()
        assertEquals(ConnectionIndicator.AUTH_FAILED, vm.connectionIndicator.value)

        link.state.value = ConnectionState.Connected(HubInfo("거실", "living"))
        runCurrent()
        assertEquals(ConnectionIndicator.NONE, vm.connectionIndicator.value)
    }

    /** 가상 시간(스케줄러)에 맞춰 흐르는 시각. delay 와 시각이 어긋나지 않게 한다. */
    private class SchedulerTimeSource(
        private val scheduler: TestCoroutineScheduler,
        private val start: ZonedDateTime,
    ) : TimeSource {
        override fun now(): ZonedDateTime =
            ZonedDateTime.ofInstant(
                Instant.from(start).plusMillis(scheduler.currentTime),
                start.zone,
            )
    }

    private class FakeDisplayConfig(
        private val is24Hour: Boolean,
        private val locale: Locale,
        private val pattern: String,
    ) : ClockDisplayConfig {
        override fun is24Hour() = is24Hour
        override fun locale() = locale
        override fun datePattern() = pattern
    }
}
