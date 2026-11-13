package app.cozybuddy.hub.core.network

import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.protocol.v1.ClientKind
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.alarmSyncRequest
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.ping
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlin.random.Random
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** 실제 WebSocket(MockWebServer)로 연결 계층을 검증한다. 타이밍 값은 테스트용으로 줄여 쓴다. */
class HubConnectionTest {

    private val server = ScriptedServer()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val settings = MutableStateFlow(ServerConfig())
    private val timeSource = object : TimeSource {
        override fun now(): ZonedDateTime = ZonedDateTime.now(ZoneId.of("Asia/Seoul"))
    }

    @AfterEach
    fun tearDown() {
        scope.cancel()
        server.close()
    }

    private fun connection(
        config: HubConnectionConfig = HubConnectionConfig(pingIntervalMs = 10_000),
        backoff: BackoffPolicy = BackoffPolicy(baseMs = 50, maxMs = 200, jitterRatio = 0.0),
    ) = HubConnection(OkHttpClient(), settings, scope, timeSource, "9.9.9", backoff, config)

    private fun configFor(token: String = "secret-token") = ServerConfig(server.url, "hub-test-01", token)

    private suspend fun HubConnection.awaitState(predicate: (ConnectionState) -> Boolean): ConnectionState =
        withTimeout(ScriptedServer.TIMEOUT_MS) { state.first(predicate) }

    @Test
    fun `hello is the first frame with the expected fields`() = runBlocking {
        server.start()
        val connection = connection()
        settings.value = configFor()
        connection.start()

        val first = server.next()

        assertEquals(Envelope.BodyCase.CLIENT_HELLO, first.bodyCase)
        assertEquals(1L, first.seq)
        with(first.clientHello) {
            assertEquals(ClientKind.CLIENT_KIND_HUB, kind)
            assertEquals("hub-test-01", clientId)
            assertEquals("secret-token", token)
            assertEquals("9.9.9", appVersion)
            assertEquals(1, protocolVersion)
        }
    }

    @Test
    fun `server hello moves to connected with the hub config`() = runBlocking {
        server.start()
        val connection = connection()
        settings.value = configFor()
        connection.start()

        val state = connection.awaitState { it is ConnectionState.Connected }

        assertEquals(ConnectionState.Connected(HubInfo("거실 허브", "living")), state)
    }

    @Test
    fun `incomplete settings never open a socket`() = runBlocking {
        server.start()
        val connection = connection()
        connection.start()

        delay(200)

        assertEquals(ConnectionState.Disconnected, connection.state.value)
        assertEquals(0, server.connectionCount)
    }

    @Test
    fun `unauthenticated error stops reconnecting until settings change`() = runBlocking {
        server.start(upgrades = 3)
        server.helloMode = ScriptedServer.HelloMode.REJECT_UNAUTHENTICATED
        val connection = connection()
        settings.value = configFor(token = "wrong")
        connection.start()

        connection.awaitState { it == ConnectionState.AuthFailed }
        delay(500)
        assertEquals(1, server.connectionCount)
        assertEquals(ConnectionState.AuthFailed, connection.state.value)

        server.helloMode = ScriptedServer.HelloMode.ACCEPT
        settings.value = configFor(token = "right")

        connection.awaitState { it is ConnectionState.Connected }
        assertEquals(2, server.connectionCount)
    }

    @Test
    fun `lost connection reconnects after a growing delay`() = runBlocking {
        server.start(upgrades = 4)
        server.helloMode = ScriptedServer.HelloMode.CLOSE_SILENTLY
        val backoff = BackoffPolicy(baseMs = 100, maxMs = 400, jitterRatio = 0.0)
        val connection = connection(backoff = backoff)
        settings.value = configFor()
        connection.start()

        withTimeout(ScriptedServer.TIMEOUT_MS) {
            while (server.connectionCount < 4) delay(20)
        }

        val gaps = server.openedAtMs.zipWithNext { a, b -> b - a }
        assertTrue(gaps[0] >= 100, "first retry waits the base delay: $gaps")
        assertTrue(gaps[1] >= 200, "second retry doubles: $gaps")
        assertTrue(gaps[2] >= 400, "third retry doubles again: $gaps")
        assertTrue(gaps[2] > gaps[0], "delays grow: $gaps")
    }

    @Test
    fun `missed pongs mark the connection dead and trigger a reconnect`() = runBlocking {
        server.start(upgrades = 2)
        server.autoPong = false
        val connection = connection(config = HubConnectionConfig(pingIntervalMs = 100, maxMissedPongs = 2))
        settings.value = configFor()
        connection.start()

        withTimeout(ScriptedServer.TIMEOUT_MS) {
            while (server.connectionCount < 2) delay(20)
        }

        val frames = generateSequence { server.received.tryReceive().getOrNull() }.toList()
        val pings = frames.filter { it.bodyCase == Envelope.BodyCase.PING }
        assertTrue(pings.size >= 2, "two pings go unanswered before the link is dropped: ${pings.size}")
    }

    @Test
    fun `missing server hello times out and reconnects`() = runBlocking {
        server.start(upgrades = 2)
        server.helloMode = ScriptedServer.HelloMode.IGNORE
        val connection = connection(config = HubConnectionConfig(pingIntervalMs = 10_000, handshakeTimeoutMs = 150))
        settings.value = configFor()
        connection.start()

        withTimeout(ScriptedServer.TIMEOUT_MS) {
            while (server.connectionCount < 2) delay(20)
        }

        assertTrue(connection.state.value !is ConnectionState.Connected)
    }

    @Test
    fun `answered pings keep the connection alive`() = runBlocking {
        server.start()
        val connection = connection(config = HubConnectionConfig(pingIntervalMs = 60, maxMissedPongs = 2))
        settings.value = configFor()
        connection.start()
        connection.awaitState { it is ConnectionState.Connected }

        delay(500)

        assertEquals(1, server.connectionCount)
        assertTrue(connection.state.value is ConnectionState.Connected)
    }

    @Test
    fun `server ping is answered with pong`() = runBlocking {
        server.start()
        val connection = connection()
        settings.value = configFor()
        connection.start()
        connection.awaitState { it is ConnectionState.Connected }
        server.next()

        server.push(envelope { ping = ping { sentEpochMs = 1234 } })

        val reply = server.next()
        assertEquals(Envelope.BodyCase.PONG, reply.bodyCase)
        assertEquals(1234L, reply.pong.pingSentEpochMs)
    }

    @Test
    fun `seq increases by one for every outgoing frame`() = runBlocking {
        server.start()
        val connection = connection()
        settings.value = configFor()
        connection.start()
        connection.awaitState { it is ConnectionState.Connected }
        val payload = envelope { ping = ping { sentEpochMs = 1 } }

        assertTrue(connection.send(payload))
        assertTrue(connection.send(payload))

        assertEquals(listOf(1L, 2L, 3L), List(3) { server.next().seq })
    }

    @Test
    fun `inbound messages are published and send is refused while offline`() = runBlocking {
        server.start()
        val connection = connection()
        assertFalse(connection.send(envelope { ping = ping { sentEpochMs = 1 } }))
        val inbound = Collected()
        val collector = scope.launch { connection.inbound.collect { inbound.add(it) } }
        settings.value = configFor()
        connection.start()
        connection.awaitState { it is ConnectionState.Connected }

        server.push(envelope { alarmSyncRequest = alarmSyncRequest { hubId = "hub-test-01" } })

        withTimeout(ScriptedServer.TIMEOUT_MS) {
            while (inbound.items.none { it.bodyCase == Envelope.BodyCase.ALARM_SYNC_REQUEST }) delay(20)
        }
        collector.cancel()
    }

    @Test
    fun `backoff doubles from one second and caps at thirty seconds`() {
        val policy = BackoffPolicy(jitterRatio = 0.0)

        val schedule = (0..7).map { policy.delayMs(it) }

        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L, 30_000L), schedule)
    }

    @Test
    fun `backoff jitter only adds within the configured ratio`() {
        val low = BackoffPolicy(jitterRatio = 0.2, random = FixedRandom(0.0))
        val high = BackoffPolicy(jitterRatio = 0.2, random = FixedRandom(0.999999))

        assertEquals(2_000L, low.delayMs(1))
        assertTrue(high.delayMs(1) in 2_000L..2_400L)
        assertTrue(high.delayMs(10) in 30_000L..36_000L)
    }

    private class FixedRandom(private val value: Double) : Random() {
        override fun nextBits(bitCount: Int): Int = 0
        override fun nextDouble(): Double = value
    }

    private class Collected {
        val items = java.util.concurrent.CopyOnWriteArrayList<Envelope>()
        fun add(item: Envelope) = items.add(item)
    }
}
