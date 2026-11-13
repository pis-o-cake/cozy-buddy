package app.cozybuddy.hub.feature.dashboard

import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.hub.core.network.HubInfo
import app.cozybuddy.hub.data.home.CommandFailure
import app.cozybuddy.hub.data.home.CommandOutcome
import app.cozybuddy.hub.data.home.DeviceCommandSender
import app.cozybuddy.hub.data.home.HomeRepository
import app.cozybuddy.protocol.v1.area
import app.cozybuddy.protocol.v1.entity
import app.cozybuddy.protocol.v1.entityState
import app.cozybuddy.protocol.v1.entityStateChanged
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.homeSnapshot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DashboardViewModelTest {

    private class Sent(val entityId: String, val action: String, val params: Map<String, String>)

    private class FakeSender : DeviceCommandSender {
        val sent = mutableListOf<Sent>()
        var reply = CompletableDeferred<CommandOutcome>()

        override suspend fun send(entityId: String, action: String, params: Map<String, String>): CommandOutcome {
            sent += Sent(entityId, action, params)
            return reply.await()
        }
    }

    private val link = FakeHubLink()
    private val sender = FakeSender()

    @BeforeEach
    fun setUp() {
        Dispatchers.setMain(StandardTestDispatcher())
    }

    @AfterEach
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun TestScope.createViewModel(): Pair<DashboardViewModel, HomeRepository> {
        val repository = HomeRepository(link, backgroundScope)
        val viewModel = DashboardViewModel(repository, link, sender)
        backgroundScope.launch { viewModel.uiState.collect { } }
        link.connect()
        repository.handle(snapshot())
        runCurrent()
        return viewModel to repository
    }

    private fun snapshot() = envelope {
        homeSnapshot = homeSnapshot {
            areas.add(area { areaId = "living"; name = "거실" })
            entities.add(
                entity {
                    entityId = "light.lamp"; areaId = "living"; name = "스탠드"; domain = "light"
                    state = entityState { state = "off"; changedEpochMs = 1 }
                },
            )
        }
    }

    private fun stateChange(newState: String) = envelope {
        entityStateChanged = entityStateChanged {
            entityId = "light.lamp"
            state = entityState { state = newState; changedEpochMs = 2 }
        }
    }

    private fun DashboardViewModel.lamp() = uiState.value.sections.single().cards.single()

    @Test
    fun `toggle sets pending and the authoritative state change clears it`() = runTest {
        val (viewModel, repository) = createViewModel()

        viewModel.onPrimaryAction("light.lamp")
        runCurrent()
        assertEquals("turn_on", sender.sent.single().action)
        assertTrue(viewModel.lamp().pending)
        assertEquals(CardState.OFF, viewModel.lamp().state)

        sender.reply.complete(CommandOutcome.Success)
        runCurrent()
        assertTrue(viewModel.lamp().pending)

        repository.handle(stateChange("on"))
        runCurrent()
        assertFalse(viewModel.lamp().pending)
        assertEquals(CardState.ON, viewModel.lamp().state)
        assertNull(viewModel.uiState.value.error)
    }

    @Test
    fun `pending is released after the wait window when no state change arrives`() = runTest {
        val (viewModel, _) = createViewModel()
        sender.reply.complete(CommandOutcome.Success)

        viewModel.onPrimaryAction("light.lamp")
        runCurrent()
        assertTrue(viewModel.lamp().pending)

        advanceTimeBy(DashboardViewModel.STATE_WAIT_MS + 1)
        runCurrent()
        assertFalse(viewModel.lamp().pending)
    }

    @Test
    fun `failure clears pending, keeps the old state and exposes the error`() = runTest {
        val (viewModel, _) = createViewModel()

        viewModel.onPrimaryAction("light.lamp")
        runCurrent()
        sender.reply.complete(CommandOutcome.Failure(CommandFailure.UNAVAILABLE))
        runCurrent()

        assertFalse(viewModel.lamp().pending)
        assertEquals(CardState.OFF, viewModel.lamp().state)
        assertEquals(CommandFailure.UNAVAILABLE, viewModel.uiState.value.error)

        viewModel.onErrorShown()
        runCurrent()
        assertNull(viewModel.uiState.value.error)
    }

    @Test
    fun `a card with a command in flight ignores further taps`() = runTest {
        val (viewModel, _) = createViewModel()

        viewModel.onPrimaryAction("light.lamp")
        runCurrent()
        viewModel.onPrimaryAction("light.lamp")
        runCurrent()

        assertEquals(1, sender.sent.size)
    }

    @Test
    fun `brightness changes are debounced into one command with the last value`() = runTest {
        val (viewModel, _) = createViewModel()

        viewModel.onBrightnessChange("light.lamp", 10)
        advanceTimeBy(100)
        viewModel.onBrightnessChange("light.lamp", 40)
        advanceTimeBy(100)
        viewModel.onBrightnessChange("light.lamp", 70)
        advanceTimeBy(DashboardViewModel.BRIGHTNESS_DEBOUNCE_MS - 1)
        runCurrent()
        assertTrue(sender.sent.isEmpty())

        advanceTimeBy(2)
        runCurrent()
        val command = sender.sent.single()
        assertEquals("set_brightness", command.action)
        assertEquals(mapOf("brightness_pct" to "70"), command.params)
    }

    @Test
    fun `hub area from the server hello is listed first and survives a disconnect`() = runTest {
        val (viewModel, repository) = createViewModel()
        repository.handle(
            envelope {
                homeSnapshot = homeSnapshot {
                    areas.add(area { areaId = "a"; name = "가" })
                    areas.add(area { areaId = "b"; name = "나" })
                    entities.add(entity { entityId = "switch.a"; areaId = "a"; name = "A"; domain = "switch"; state = entityState { state = "off" } })
                    entities.add(entity { entityId = "switch.b"; areaId = "b"; name = "B"; domain = "switch"; state = entityState { state = "off" } })
                }
            },
        )
        link.state.value = ConnectionState.Connected(HubInfo("허브", "b"))
        runCurrent()

        assertEquals(listOf("b", "a"), viewModel.uiState.value.sections.map { it.areaId })
        assertTrue(viewModel.uiState.value.controlsEnabled)

        link.state.value = ConnectionState.Disconnected
        runCurrent()
        assertEquals(listOf("b", "a"), viewModel.uiState.value.sections.map { it.areaId })
        assertFalse(viewModel.uiState.value.controlsEnabled)
    }

    @Test
    fun `inactivity returns to ambient after 60 seconds and interaction restarts the timer`() = runTest {
        val (viewModel, _) = createViewModel()
        val events = Channel<Unit>(Channel.UNLIMITED)
        backgroundScope.launch { viewModel.returnToAmbient.collect { events.trySend(Unit) } }
        viewModel.onScreenShown()
        runCurrent()

        advanceTimeBy(DashboardViewModel.INACTIVITY_TIMEOUT_MS - 10_000)
        viewModel.onUserInteraction()
        advanceTimeBy(DashboardViewModel.INACTIVITY_TIMEOUT_MS - 1)
        runCurrent()
        assertTrue(events.tryReceive().isFailure)

        advanceTimeBy(2)
        runCurrent()
        assertTrue(events.tryReceive().isSuccess)
        assertTrue(events.tryReceive().isFailure)
    }

    @Test
    fun `leaving the dashboard cancels the inactivity timer`() = runTest {
        val (viewModel, _) = createViewModel()
        val events = Channel<Unit>(Channel.UNLIMITED)
        backgroundScope.launch { viewModel.returnToAmbient.collect { events.trySend(Unit) } }
        viewModel.onScreenShown()
        runCurrent()

        viewModel.onScreenHidden()
        advanceTimeBy(DashboardViewModel.INACTIVITY_TIMEOUT_MS * 2)
        runCurrent()

        assertTrue(events.tryReceive().isFailure)
    }
}
