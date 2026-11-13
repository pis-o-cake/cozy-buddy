package app.cozybuddy.hub.feature.settings

import app.cozybuddy.hub.core.audio.wakeword.WakeEvent
import app.cozybuddy.hub.core.audio.wakeword.WakeSensitivity
import app.cozybuddy.hub.core.audio.wakeword.WakeWordEngine
import app.cozybuddy.hub.core.audio.wakeword.WakeWordFailure
import app.cozybuddy.hub.core.audio.wakeword.WakeWordPrefs
import app.cozybuddy.hub.core.audio.wakeword.WakeWordSettings
import app.cozybuddy.hub.core.audio.wakeword.WakeWordStatus
import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.network.UrlError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
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

    private class FakeWakeSettings(initial: WakeWordPrefs = WakeWordPrefs()) : WakeWordSettings {
        override val prefs = MutableStateFlow(initial)
        override suspend fun setEnabled(enabled: Boolean) {
            prefs.value = prefs.value.copy(enabled = enabled)
        }
        override suspend fun setSensitivity(sensitivity: Float) {
            prefs.value = prefs.value.copy(sensitivity = sensitivity)
        }
    }

    private class FakeWakeEngine : WakeWordEngine {
        override val status = MutableStateFlow<WakeWordStatus>(WakeWordStatus.Idle)
        override val events = MutableSharedFlow<WakeEvent>()
        override suspend fun initialize() = true
        override suspend fun process(frame: ShortArray) = 0f
        override fun setThreshold(threshold: Float) = Unit
        override fun reset() = Unit
        override fun release() = Unit
    }

    private val link = FakeHubLink()
    private val wakeSettings = FakeWakeSettings()
    private val wakeEngine = FakeWakeEngine()

    @BeforeEach
    fun setUp() = Dispatchers.setMain(StandardTestDispatcher())

    @AfterEach
    fun tearDown() = Dispatchers.resetMain()

    @Test
    fun `stored values prefill the form and connection state is exposed`() = runTest {
        val vm = SettingsViewModel(FakeSettings(ServerConfig("ws://192.168.0.1/ws", "hub-1", "tok")), wakeSettings, wakeEngine, link)
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
        val vm = SettingsViewModel(settings, wakeSettings, wakeEngine, link)
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
        val vm = SettingsViewModel(settings, wakeSettings, wakeEngine, link)
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

    @Test
    fun `wake word prefs prefill and toggle persists immediately`() = runTest {
        val stored = FakeWakeSettings(WakeWordPrefs(enabled = false, sensitivity = 0.9f))
        val vm = SettingsViewModel(FakeSettings(), stored, wakeEngine, link)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()
        assertEquals(false, vm.uiState.value.wakeWordEnabled)
        assertEquals(0.9f, vm.uiState.value.wakeWordSensitivity)

        vm.onWakeWordEnabledChange(true)
        runCurrent()

        assertEquals(true, stored.prefs.value.enabled)
    }

    @Test
    fun `sensitivity is saved only when the slider is released`() = runTest {
        val vm = SettingsViewModel(FakeSettings(), wakeSettings, wakeEngine, link)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()

        vm.onWakeWordSensitivityChange(0.2f)
        vm.onWakeWordSensitivityChange(0.8f)
        runCurrent()
        assertEquals(WakeSensitivity.DEFAULT, wakeSettings.prefs.value.sensitivity)
        assertEquals(0.8f, vm.uiState.value.wakeWordSensitivity)

        vm.onWakeWordSensitivityCommit()
        runCurrent()
        assertEquals(0.8f, wakeSettings.prefs.value.sensitivity)
    }

    @Test
    fun `engine failure is exposed for the settings screen`() = runTest {
        val vm = SettingsViewModel(FakeSettings(), wakeSettings, wakeEngine, link)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()

        wakeEngine.status.value = WakeWordStatus.Failed(WakeWordFailure.MODEL_CORRUPT)
        runCurrent()

        assertEquals(WakeWordStatus.Failed(WakeWordFailure.MODEL_CORRUPT), vm.uiState.value.wakeWordStatus)
    }
}
