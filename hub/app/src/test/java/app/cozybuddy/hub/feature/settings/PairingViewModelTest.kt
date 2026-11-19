package app.cozybuddy.hub.feature.settings

import app.cozybuddy.hub.core.network.OkHttpPairingClient
import app.cozybuddy.hub.core.network.PairResult
import app.cozybuddy.hub.core.network.PairedCredentials
import app.cozybuddy.hub.core.network.PairingClient
import app.cozybuddy.hub.core.network.PairingEndpoint
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.network.UrlError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PairingViewModelTest {

    private class FakeSettings(initial: ServerConfig = ServerConfig()) : ServerSettings {
        override val config = MutableStateFlow(initial)
        var failOnUpdate = false
        override suspend fun update(config: ServerConfig) {
            if (failOnUpdate) error("disk full")
            this.config.value = config
        }
    }

    private class FakePairing(var result: PairResult) : PairingClient {
        val calls = mutableListOf<Pair<PairingEndpoint, String>>()
        override suspend fun pair(endpoint: PairingEndpoint, code: String): PairResult {
            calls += endpoint to code
            return result
        }
    }

    private val settings = FakeSettings()
    private val server = MockWebServer()

    @BeforeEach
    fun setUp() = Dispatchers.setMain(StandardTestDispatcher())

    @AfterEach
    fun tearDown() {
        Dispatchers.resetMain()
        server.close()
    }

    private fun failure(vm: PairingViewModel) = (vm.uiState.value.status as PairingStatus.Failed).error

    private fun ok() = PairResult.Success(PairedCredentials("hub-1234abcd", "issued-token"))

    @Test
    fun `successful pairing stores credentials and websocket url`() = runTest {
        val pairing = FakePairing(ok())
        val vm = PairingViewModel(settings, pairing)
        vm.onUrlChange("http://192.168.0.10:8120")
        vm.onCodeChange("123456")
        vm.onPair()
        runCurrent()

        assertEquals(ServerConfig("ws://192.168.0.10:8120/ws", "hub-1234abcd", "issued-token"), settings.config.value)
        assertEquals(PairingStatus.Paired("hub-1234abcd"), vm.uiState.value.status)
        assertEquals("", vm.uiState.value.code)
        assertEquals("123456", pairing.calls.single().second)
    }

    @Test
    fun `end to end against a mock server`() = runTest {
        server.enqueue(MockResponse.Builder().body("""{"client_id":"hub-e2e","token":"e2e-token"}""").build())
        server.start()
        val vm = PairingViewModel(settings, OkHttpPairingClient(OkHttpClient(), Dispatchers.IO))
        vm.onUrlChange(server.url("/").toString())
        vm.onCodeChange("654321")
        vm.onPair()
        runCurrent()
        vm.uiState.first { it.status !is PairingStatus.InProgress }

        assertEquals("hub-e2e", settings.config.value.clientId)
        assertEquals("e2e-token", settings.config.value.token)
        assertTrue(settings.config.value.url.startsWith("ws://"))
        assertTrue(settings.config.value.url.endsWith("/ws"))
    }

    @Test
    fun `code input keeps only six digits`() = runTest {
        val vm = PairingViewModel(settings, FakePairing(ok()))
        vm.onCodeChange("12-34 56789")
        assertEquals("123456", vm.uiState.value.code)
    }

    @Test
    fun `short code is rejected before any request`() = runTest {
        val pairing = FakePairing(ok())
        val vm = PairingViewModel(settings, pairing)
        vm.onUrlChange("http://192.168.0.10:8120")
        vm.onCodeChange("123")
        vm.onPair()
        runCurrent()

        assertEquals(PairingError.CodeFormat, failure(vm))
        assertTrue(pairing.calls.isEmpty())
    }

    @Test
    fun `bad or public cleartext url is rejected before any request`() = runTest {
        val pairing = FakePairing(ok())
        val vm = PairingViewModel(settings, pairing)
        vm.onCodeChange("123456")
        vm.onUrlChange("http://cozy.example.com")
        vm.onPair()
        assertEquals(PairingError.Url(UrlError.CLEARTEXT_NOT_LOCAL), failure(vm))
        vm.onUrlChange("")
        vm.onPair()
        assertEquals(PairingError.Url(UrlError.BLANK), failure(vm))
        assertTrue(pairing.calls.isEmpty())
    }

    @Test
    fun `server and network failures map to error states and keep settings untouched`() = runTest {
        val cases = mapOf(
            PairResult.InvalidCode to PairingError.InvalidCode,
            PairResult.RateLimited to PairingError.RateLimited,
            PairResult.NetworkError to PairingError.Network,
            PairResult.ServerError to PairingError.Server,
            PairResult.BadResponse to PairingError.Server,
        )
        for ((result, expected) in cases) {
            val vm = PairingViewModel(settings, FakePairing(result))
            vm.onUrlChange("http://192.168.0.10:8120")
            vm.onCodeChange("123456")
            vm.onPair()
            runCurrent()
            assertEquals(expected, failure(vm), "for $result")
        }
        assertEquals(ServerConfig(), settings.config.value)
    }

    @Test
    fun `storage failure after pairing is reported`() = runTest {
        settings.failOnUpdate = true
        val vm = PairingViewModel(settings, FakePairing(ok()))
        vm.onUrlChange("http://192.168.0.10:8120")
        vm.onCodeChange("123456")
        vm.onPair()
        runCurrent()
        assertEquals(PairingError.Storage, failure(vm))
    }

    @Test
    fun `url is prefilled from stored settings`() = runTest {
        val vm = PairingViewModel(FakeSettings(ServerConfig("ws://192.168.0.5:8120/ws", "h", "t")), FakePairing(ok()))
        runCurrent()
        assertEquals("ws://192.168.0.5:8120/ws", vm.uiState.value.url)
    }
}
