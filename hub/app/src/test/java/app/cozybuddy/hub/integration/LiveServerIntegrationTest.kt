package app.cozybuddy.hub.integration

import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubConnection
import app.cozybuddy.hub.core.network.HubConnectionConfig
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.sync.AlarmSyncManager
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.hub.domain.repository.AlarmRepository
import app.cozybuddy.hub.domain.repository.SnoozeRepository
import app.cozybuddy.hub.domain.scheduling.AlarmScheduler
import app.cozybuddy.hub.domain.usecase.AlarmCoordinator
import app.cozybuddy.protocol.v1.AlarmKind as AlarmKindProto
import app.cozybuddy.protocol.v1.ClientKind
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.alarm
import app.cozybuddy.protocol.v1.alarmDelete
import app.cozybuddy.protocol.v1.alarmUpsert
import app.cozybuddy.protocol.v1.clientHello
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.repeating
import io.mockk.mockk
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.DayOfWeek
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Assumptions.assumeTrue
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test

/**
 * 실제 Cozy Server 에 붙는 통합 테스트. `-Pintegration` 일 때만 실행된다.
 *
 * 접속 정보는 환경변수로만 받는다(토큰을 코드·로그에 남기지 않는다).
 * COZY_IT_URL, COZY_IT_HUB_ID, COZY_IT_HUB_TOKEN, COZY_IT_MOBILE_ID, COZY_IT_MOBILE_TOKEN
 */
@Tag("integration")
class LiveServerIntegrationTest {

    private val env = System.getenv()
    private val url = env["COZY_IT_URL"].orEmpty()
    private val hubId = env["COZY_IT_HUB_ID"].orEmpty()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val http = HttpClient.newHttpClient()
    private val okHttp = OkHttpClient()
    private var mobileSocket: WebSocket? = null

    private val timeSource = object : TimeSource {
        override fun now(): ZonedDateTime = ZonedDateTime.now(ZoneId.of("Asia/Seoul"))
    }

    private class MemoryRepository : AlarmRepository {
        val items = MutableStateFlow<List<Alarm>>(emptyList())
        private var revision = 0L

        override fun observeAll(): Flow<List<Alarm>> = items
        override suspend fun get(id: String): Alarm? = items.value.firstOrNull { it.id == id }
        override suspend fun upsert(alarm: Alarm): Alarm {
            val stored = alarm.copy(revision = ++revision)
            items.value = items.value.filterNot { it.id == alarm.id } + stored
            return stored
        }

        override suspend fun delete(id: String): Boolean {
            val existed = items.value.any { it.id == id }
            if (existed) revision++
            items.value = items.value.filterNot { it.id == id }
            return existed
        }

        override suspend fun currentRevision(): Long = revision
    }

    @AfterEach
    fun tearDown() {
        mobileSocket?.cancel()
        scope.cancel()
    }

    private fun restAlarms(): String {
        val request = HttpRequest.newBuilder(URI.create("${restBase()}/api/alarm?hub_id=$hubId")).GET().build()
        return http.send(request, HttpResponse.BodyHandlers.ofString()).body()
    }

    private fun restBase(): String = url.replaceFirst("ws", "http").substringBeforeLast("/ws")

    private fun hasSynced(body: String, alarmId: String): Boolean {
        val entry = Regex("\\{[^{}]*\"alarm_id\"\\s*:\\s*\"$alarmId\"[^{}]*}").find(body)?.value ?: return false
        return Regex("\"sync_state\"\\s*:\\s*\"synced\"").containsMatchIn(entry)
    }

    private suspend fun awaitRest(what: String, condition: (String) -> Boolean) {
        withTimeout(LIVE_TIMEOUT_MS) {
            while (!condition(restAlarms())) delay(POLL_MS)
        }
        println("OK: $what")
    }

    private fun repeatingAlarm(id: String, hour: Int) = Alarm(
        id = id,
        kind = AlarmKind.ALARM,
        label = id,
        schedule = AlarmSchedule.Repeating(hour, 0, DayOfWeek.entries.toSet()),
        enabled = true,
    )

    @Test
    fun `hub connects, publishes its snapshot and applies mobile changes against the live server`() = runBlocking {
        val hubToken = env["COZY_IT_HUB_TOKEN"].orEmpty()
        assumeTrue(url.isNotEmpty() && hubId.isNotEmpty() && hubToken.isNotEmpty(), "live server env not set")

        val repository = MemoryRepository()
        val coordinator = AlarmCoordinator(repository, mockk<SnoozeRepository>(relaxed = true), mockk<AlarmScheduler>(relaxed = true), timeSource)
        coordinator.save(repeatingAlarm("it-alarm-1", 6))
        coordinator.save(repeatingAlarm("it-alarm-2", 7))

        val settings = object : ServerSettings {
            override val config = MutableStateFlow(ServerConfig(url, hubId, hubToken))
            override suspend fun update(config: ServerConfig) {
                this.config.value = config
            }
        }
        val connection = HubConnection(okHttp, settings.config, scope, timeSource, "integration", config = HubConnectionConfig())
        val sync = AlarmSyncManager(connection, repository, coordinator, settings, timeSource, scope)
        sync.start()
        connection.start()

        val connected = withTimeout(LIVE_TIMEOUT_MS) { connection.state.first { it is ConnectionState.Connected } }
        assertNotNull((connected as ConnectionState.Connected).hubInfo.name)
        println("OK: connected, hub name='${connected.hubInfo.name}' area='${connected.hubInfo.areaId}'")

        awaitRest("server holds hub snapshot as synced") { hasSynced(it, "it-alarm-1") && hasSynced(it, "it-alarm-2") }

        val mobile = openMobile()
        mobile.send(upsertFromMobile("it-from-mobile", 9))
        awaitRest("mobile upsert reached hub and was acked") { hasSynced(it, "it-from-mobile") }
        assertTrue(repository.get("it-from-mobile") != null)

        mobile.send(deleteFromMobile("it-from-mobile"))
        awaitRest("mobile delete reached hub and was acked") { !it.contains("it-from-mobile") }
        assertEquals(null, repository.get("it-from-mobile"))
        Unit
    }

    private class Mobile(val socket: WebSocket, private val seq: java.util.concurrent.atomic.AtomicLong) {
        fun send(envelope: Envelope) {
            val numbered = envelope.toBuilder().setSeq(seq.incrementAndGet()).build()
            socket.send(numbered.toByteArray().toByteString())
        }
    }

    private suspend fun openMobile(): Mobile {
        val mobileId = env["COZY_IT_MOBILE_ID"].orEmpty()
        val mobileToken = env["COZY_IT_MOBILE_TOKEN"].orEmpty()
        val frames = Channel<Envelope>(Channel.UNLIMITED)
        val opened = CompletableDeferred<Unit>()
        val socket = okHttp.newWebSocket(
            Request.Builder().url(url).build(),
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                    frames.trySend(Envelope.parseFrom(bytes.toByteArray()))
                }

                override fun onOpen(webSocket: WebSocket, response: Response) {
                    opened.complete(Unit)
                }
            },
        )
        mobileSocket = socket
        val mobile = Mobile(socket, java.util.concurrent.atomic.AtomicLong(0))
        withTimeout(LIVE_TIMEOUT_MS) { opened.await() }
        mobile.send(
            envelope {
                clientHello = clientHello {
                    kind = ClientKind.CLIENT_KIND_MOBILE
                    clientId = mobileId
                    token = mobileToken
                    appVersion = "integration"
                    protocolVersion = 1
                }
            },
        )
        val reply = withTimeout(LIVE_TIMEOUT_MS) { frames.receive() }
        assertEquals(Envelope.BodyCase.SERVER_HELLO, reply.bodyCase)
        println("OK: mobile client accepted")
        return mobile
    }

    private fun upsertFromMobile(id: String, hour: Int) = envelope {
        alarmUpsert = alarmUpsert {
            requestId = "it-req-upsert"
            alarm = alarm {
                alarmId = id
                kind = AlarmKindProto.ALARM_KIND_ALARM
                label = id
                enabled = true
                hubId = this@LiveServerIntegrationTest.hubId
                repeating = repeating {
                    this.hour = hour
                    minute = 15
                }
            }
        }
    }

    private fun deleteFromMobile(id: String) = envelope {
        alarmDelete = alarmDelete {
            requestId = "it-req-delete"
            alarmId = id
            hubId = this@LiveServerIntegrationTest.hubId
        }
    }

    private companion object {
        const val LIVE_TIMEOUT_MS = 15_000L
        const val POLL_MS = 250L
    }
}
