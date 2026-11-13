package app.cozybuddy.hub.feature.settings

import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.network.UrlError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {

    private class FakeSettings(initial: ServerConfig = ServerConfig()) : ServerSettings {
        override val config = MutableStateFlow(initial)
        override suspend fun update(config: ServerConfig) {
            this.config.value = config
        }
    }

    private val link = FakeHubLink()

    @BeforeEach
    fun setUp() = Dispatchers.setMain(StandardTestDispatcher())

    @AfterEach
    fun tearDown() = Dispatchers.resetMain()

    @Test
    fun `stored values prefill the form and connection state is exposed`() = runTest {
        val vm = SettingsViewModel(FakeSettings(ServerConfig("ws://192.168.0.1/ws", "hub-1", "tok")), link)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()
        link.state.value = ConnectionState.AuthFailed
        runCurrent()

        assertEquals("hub-1", vm.uiState.value.clientId)
        assertEquals(ConnectionState.AuthFailed, vm.uiState.value.connection)
    }

    @Test
    fun `invalid input is reported and nothing is saved`() = runTest {
        val settings = FakeSettings()
        val vm = SettingsViewModel(settings, link)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()

        vm.onUrlChange("ws://example.com/ws")
        vm.onSave()
        runCurrent()

        val errors = vm.uiState.value.errors
        assertEquals(UrlError.CLEARTEXT_NOT_LOCAL, errors.url)
        assertTrue(errors.clientIdBlank && errors.tokenBlank)
        assertEquals(ServerConfig(), settings.config.value)
    }

    @Test
    fun `valid input is saved`() = runTest {
        val settings = FakeSettings()
        val vm = SettingsViewModel(settings, link)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()

        vm.onUrlChange("ws://192.168.0.171:8120/ws")
        vm.onClientIdChange("hub-dev-01")
        vm.onTokenChange("tok")
        vm.onSave()
        runCurrent()

        assertEquals(ServerConfig("ws://192.168.0.171:8120/ws", "hub-dev-01", "tok"), settings.config.value)
        assertTrue(vm.uiState.value.saved)
    }
}
