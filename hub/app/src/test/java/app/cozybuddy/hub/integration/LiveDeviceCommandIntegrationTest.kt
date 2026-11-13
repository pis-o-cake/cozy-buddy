package app.cozybuddy.hub.integration

import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubConnection
import app.cozybuddy.hub.core.network.HubConnectionConfig
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.home.CommandOutcome
import app.cozybuddy.hub.data.home.HomeRepository
import app.cozybuddy.hub.data.home.LinkDeviceCommandSender
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Assumptions.assumeTrue
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test

/**
 * 실제 서버(와 그 뒤의 Home Assistant)로 기기 제어 한 건을 왕복하는 통합 테스트. `-Pintegration` 일 때만 실행된다.
 *
 * 환경변수: COZY_IT_URL, COZY_IT_HUB_ID, COZY_IT_HUB_TOKEN, 선택으로 COZY_IT_ENTITY(기본 light.ceiling_lights).
 * 기기를 한 번 토글하고 원래 상태로 되돌린다.
 */
@Tag("integration")
class LiveDeviceCommandIntegrationTest {

    private val env = System.getenv()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val timeSource = object : TimeSource {
        override fun now(): ZonedDateTime = ZonedDateTime.now(ZoneId.of("Asia/Seoul"))
    }

    @AfterEach
    fun tearDown() {
        scope.cancel()
    }

    @Test
    fun `toggling a light through the command sender yields ok and a state change`() = runBlocking {
        val url = env["COZY_IT_URL"].orEmpty()
        val hubId = env["COZY_IT_HUB_ID"].orEmpty()
        val token = env["COZY_IT_HUB_TOKEN"].orEmpty()
        assumeTrue(url.isNotEmpty() && hubId.isNotEmpty() && token.isNotEmpty(), "live server env not set")
        val entityId = env["COZY_IT_ENTITY"] ?: "light.ceiling_lights"

        val config = MutableStateFlow(ServerConfig(url, hubId, token))
        val connection = HubConnection(OkHttpClient(), config, scope, timeSource, "integration", config = HubConnectionConfig())
        val home = HomeRepository(connection, scope)
        home.start()
        val seen = java.util.concurrent.CopyOnWriteArrayList<String>()
        scope.launch(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) {
            connection.inbound.collect { seen += "${it.bodyCase}:${it.homeSnapshot.entitiesCount}" }
        }
        connection.start()

        withTimeout(LIVE_TIMEOUT_MS) { connection.state.first { it is ConnectionState.Connected } }
        withTimeoutOrNull(LIVE_TIMEOUT_MS * 2) { home.state.first { it.entities.isNotEmpty() } }
        assertTrue(home.state.value.entities.isNotEmpty(), "server sent no home entities (is the Home Assistant bridge up?) received=$seen")
        assertTrue(entityId in home.state.value.entities, "entity $entityId missing; lights: ${home.state.value.entities.keys.filter { it.startsWith("light.") }}")
        println("OK: snapshot has ${home.state.value.entities.size} entities in ${home.state.value.areas.size} areas")

        val sender = LinkDeviceCommandSender(connection)
        val original = home.state.value.entities.getValue(entityId).state.state
        assertNotEquals("unavailable", original)
        val flip = if (original == "on") "turn_off" else "turn_on"
        val back = if (original == "on") "turn_on" else "turn_off"

        try {
            assertEquals(CommandOutcome.Success, sender.send(entityId, flip))
            val changed = withTimeout(LIVE_TIMEOUT_MS) { home.state.first { it.entities[entityId]?.state?.state != original } }
            println("OK: $entityId $original -> ${changed.entities.getValue(entityId).state.state}")
        } finally {
            assertEquals(CommandOutcome.Success, sender.send(entityId, back))
            withTimeout(LIVE_TIMEOUT_MS) { home.state.first { it.entities[entityId]?.state?.state == original } }
            println("OK: $entityId restored to $original")
        }
        Unit
    }

    private companion object {
        const val LIVE_TIMEOUT_MS = 15_000L
    }
}
