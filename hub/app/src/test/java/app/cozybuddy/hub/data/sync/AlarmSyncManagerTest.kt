package app.cozybuddy.hub.data.sync

import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.hub.domain.repository.AlarmRepository
import app.cozybuddy.hub.domain.repository.SnoozeRepository
import app.cozybuddy.hub.domain.scheduling.AlarmScheduler
import app.cozybuddy.hub.domain.usecase.AlarmCoordinator
import app.cozybuddy.protocol.v1.AlarmKind as AlarmKindProto
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.ErrorCode
import app.cozybuddy.protocol.v1.alarm
import app.cozybuddy.protocol.v1.alarmDelete
import app.cozybuddy.protocol.v1.alarmSyncRequest
import app.cozybuddy.protocol.v1.alarmUpsert
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.repeating
import io.mockk.mockk
import java.time.DayOfWeek
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AlarmSyncManagerTest {

    private class FakeRepository : AlarmRepository {
        val items = MutableStateFlow<List<Alarm>>(emptyList())
        var revision = 0L

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

    private class FakeSettings(clientId: String) : ServerSettings {
        override val config = MutableStateFlow(ServerConfig("ws://127.0.0.1/ws", clientId, "t"))
        override suspend fun update(config: ServerConfig) {
            this.config.value = config
        }
    }

    private val now = ZonedDateTime.of(2026, 11, 13, 9, 0, 0, 0, ZoneId.of("Asia/Seoul"))
    private val timeSource = object : TimeSource {
        override fun now(): ZonedDateTime = now
    }
    private val link = FakeHubLink()
    private val repository = FakeRepository()
    private val coordinator = AlarmCoordinator(repository, mockk<SnoozeRepository>(relaxed = true), mockk<AlarmScheduler>(relaxed = true), timeSource)

    private fun TestScope.manager(): AlarmSyncManager {
        val manager = AlarmSyncManager(
            link = link,
            repository = repository,
            coordinator = coordinator,
            settings = FakeSettings("hub-test-01"),
            timeSource = timeSource,
            scope = backgroundScope,
        )
        manager.start()
        runCurrent()
        return manager
    }

    private fun daily(id: String, hour: Int = 7) = Alarm(
        id = id,
        kind = AlarmKind.ALARM,
        label = id,
        schedule = AlarmSchedule.Repeating(hour, 0, DayOfWeek.entries.toSet()),
        enabled = true,
    )

    private fun upsertEnvelope(requestId: String, id: String, hour: Int = 6) = envelope {
        alarmUpsert = alarmUpsert {
            this.requestId = requestId
            alarm = alarm {
                alarmId = id
                kind = AlarmKindProto.ALARM_KIND_ALARM
                label = "서버에서"
                enabled = true
                revision = 9_999
                hubId = "hub-test-01"
                repeating = repeating {
                    this.hour = hour
                    minute = 30
                }
            }
        }
    }

    private fun List<Envelope>.ofCase(case: Envelope.BodyCase) = filter { it.bodyCase == case }

    @Test
    fun `sync request is answered with a snapshot of every alarm`() = runTest {
        repository.upsert(daily("a-1"))
        repository.upsert(daily("a-2", hour = 8))
        manager()
        link.connect()
        runCurrent()

        link.receive(envelope { alarmSyncRequest = alarmSyncRequest { hubId = "hub-test-01" } })
        runCurrent()

        val snapshot = link.sent.single().alarmSnapshot
        assertEquals("hub-test-01", snapshot.hubId)
        assertEquals(setOf("a-1", "a-2"), snapshot.alarmsList.map { it.alarmId }.toSet())
        assertEquals(listOf(1L, 2L), snapshot.alarmsList.map { it.revision }.sorted())
        assertTrue(snapshot.alarmsList.all { it.hubId == "hub-test-01" })
    }

    @Test
    fun `connecting alone does not push a snapshot`() = runTest {
        repository.upsert(daily("a-1"))
        manager()

        link.connect()
        advanceTimeBy(2_000)
        runCurrent()

        assertTrue(link.sent.isEmpty())
    }

    @Test
    fun `upsert is applied with a hub revision and acked`() = runTest {
        repository.upsert(daily("old"))
        manager()
        link.connect()
        runCurrent()

        link.receive(upsertEnvelope("req-1", "srv-1"))
        runCurrent()

        val stored = repository.get("srv-1")!!
        assertEquals("서버에서", stored.label)
        assertEquals(2L, stored.revision)
        val ack = link.sent.single().alarmAck
        assertEquals("req-1", ack.requestId)
        assertEquals("srv-1", ack.alarmId)
        assertEquals(2L, ack.revision)
        assertTrue(ack.ok)
    }

    @Test
    fun `server originated change does not echo a snapshot`() = runTest {
        manager()
        link.connect()
        runCurrent()

        link.receive(upsertEnvelope("req-1", "srv-1"))
        advanceTimeBy(2_000)
        runCurrent()

        assertEquals(1, link.sent.size)
        assertEquals(1, link.sent.ofCase(Envelope.BodyCase.ALARM_ACK).size)
        assertTrue(link.sent.ofCase(Envelope.BodyCase.ALARM_SNAPSHOT).isEmpty())
    }

    @Test
    fun `invalid payload is rejected with bad request`() = runTest {
        manager()
        link.connect()
        runCurrent()

        link.receive(envelope { alarmUpsert = alarmUpsert { requestId = "req-bad" } })
        link.receive(upsertEnvelope("req-hour", "srv-2", hour = 30))
        runCurrent()

        val acks = link.sent.map { it.alarmAck }
        assertEquals(listOf("req-bad", "req-hour"), acks.map { it.requestId })
        assertTrue(acks.all { !it.ok && it.code == ErrorCode.ERROR_CODE_BAD_REQUEST })
        assertNull(repository.get("srv-2"))
    }

    @Test
    fun `delete removes the alarm and acks with the hub revision`() = runTest {
        repository.upsert(daily("a-1"))
        manager()
        link.connect()
        runCurrent()

        link.receive(envelope { alarmDelete = alarmDelete { requestId = "req-d"; alarmId = "a-1"; hubId = "hub-test-01" } })
        advanceTimeBy(2_000)
        runCurrent()

        assertNull(repository.get("a-1"))
        val ack = link.sent.single().alarmAck
        assertTrue(ack.ok)
        assertEquals("req-d", ack.requestId)
        assertEquals(repository.revision, ack.revision)
    }

    @Test
    fun `deleting an unknown alarm is acknowledged as done`() = runTest {
        manager()
        link.connect()
        runCurrent()

        link.receive(envelope { alarmDelete = alarmDelete { requestId = "req-d"; alarmId = "ghost" } })
        runCurrent()

        assertTrue(link.sent.single().alarmAck.ok)
    }

    @Test
    fun `local change while connected sends exactly one snapshot`() = runTest {
        manager()
        link.connect()
        runCurrent()

        coordinator.save(daily("a-1"))
        advanceTimeBy(100)
        coordinator.save(daily("a-2", hour = 8))
        advanceTimeBy(2_000)
        runCurrent()

        val snapshot = link.sent.single().alarmSnapshot
        assertEquals(2, snapshot.alarmsCount)
    }

    @Test
    fun `local change while disconnected sends nothing and reconnect waits for the sync request`() = runTest {
        manager()

        coordinator.save(daily("a-1"))
        advanceTimeBy(2_000)
        link.connect()
        advanceTimeBy(2_000)
        runCurrent()

        assertTrue(link.sent.isEmpty())
    }
}
