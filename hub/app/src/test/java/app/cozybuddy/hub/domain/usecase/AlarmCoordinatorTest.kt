package app.cozybuddy.hub.domain.usecase

import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.hub.domain.repository.AlarmRepository
import app.cozybuddy.hub.domain.scheduling.AlarmScheduler
import io.mockk.mockk
import io.mockk.verify
import java.time.DayOfWeek
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class AlarmCoordinatorTest {

    private val zone = ZoneId.of("Asia/Seoul")
    private val now = ZonedDateTime.of(2026, 11, 13, 9, 0, 0, 0, zone)
    private val timeSource = object : TimeSource {
        override fun now(): ZonedDateTime = now
    }

    private class FakeRepository : AlarmRepository {
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
            items.value = items.value.filterNot { it.id == id }
            return existed
        }
    }

    private val repository = FakeRepository()
    private val scheduler = mockk<AlarmScheduler>(relaxed = true)
    private val coordinator = AlarmCoordinator(repository, scheduler, timeSource)

    private fun daily(id: String, hour: Int, minute: Int, enabled: Boolean = true) = Alarm(
        id = id,
        kind = AlarmKind.ALARM,
        label = id,
        schedule = AlarmSchedule.Repeating(hour, minute, setOf(DayOfWeek.entries.first { it == now.dayOfWeek }, now.dayOfWeek.plus(1))),
        enabled = enabled,
    )

    private fun oneShot(id: String, at: ZonedDateTime, kind: AlarmKind = AlarmKind.TIMER) =
        Alarm(id, kind, "", AlarmSchedule.OneShot(at.toInstant().toEpochMilli()), enabled = true)

    @Test
    fun `saving an enabled alarm schedules its next fire time`() = runBlocking {
        coordinator.save(daily("a", 18, 30))

        verify(exactly = 1) { scheduler.schedule("a", now.withHour(18).withMinute(30).toInstant()) }
    }

    @Test
    fun `saving a disabled alarm cancels instead of scheduling`() = runBlocking {
        coordinator.save(daily("a", 18, 30, enabled = false))

        verify(exactly = 1) { scheduler.cancel("a") }
        verify(exactly = 0) { scheduler.schedule(any(), any()) }
    }

    @Test
    fun `disabling an existing alarm cancels its schedule`() = runBlocking {
        coordinator.save(daily("a", 18, 30))

        coordinator.setEnabled("a", false)

        verify(exactly = 1) { scheduler.cancel("a") }
        assertFalse(repository.get("a")!!.enabled)
    }

    @Test
    fun `deleting removes the row and cancels the schedule`() = runBlocking {
        coordinator.save(daily("a", 18, 30))

        coordinator.delete("a")

        assertEquals(null, repository.get("a"))
        verify(exactly = 1) { scheduler.cancel("a") }
    }

    @Test
    fun `saving a one-shot already in the past stores it disabled`() = runBlocking {
        val stored = coordinator.save(oneShot("t", now.minusMinutes(1)))

        assertFalse(stored.enabled)
        verify(exactly = 0) { scheduler.schedule(any(), any()) }
    }

    @Test
    fun `rescheduleAll schedules live alarms and disables expired one-shots`() = runBlocking {
        repository.upsert(daily("a", 18, 30))
        repository.upsert(oneShot("expired", now.minusHours(1)))
        repository.upsert(oneShot("live", now.plusHours(1)))

        coordinator.rescheduleAll()

        verify { scheduler.schedule("a", now.withHour(18).withMinute(30).toInstant()) }
        verify { scheduler.schedule("live", now.plusHours(1).toInstant()) }
        verify { scheduler.cancel("expired") }
        assertFalse(repository.get("expired")!!.enabled)
        assertTrue(repository.get("live")!!.enabled)
    }

    @Test
    fun `finishing a one-shot disables it`() = runBlocking {
        repository.upsert(oneShot("t", now.plusMinutes(1)))

        coordinator.onRingFinished("t", snoozed = false)

        assertFalse(repository.get("t")!!.enabled)
        verify { scheduler.cancel("t") }
    }

    @Test
    fun `finishing a repeating alarm leaves it enabled`() = runBlocking {
        repository.upsert(daily("a", 18, 30))

        coordinator.onRingFinished("a", snoozed = false)

        assertTrue(repository.get("a")!!.enabled)
        verify(exactly = 0) { scheduler.cancel(any()) }
    }

    @Test
    fun `firing a repeating alarm re-arms the next occurrence`() = runBlocking {
        repository.upsert(daily("a", 8, 0))

        val fired = coordinator.onFired("a")

        assertEquals("a", fired?.id)
        // 오늘 08:00 은 지났으므로 다음 요일(내일) 08:00.
        verify { scheduler.schedule("a", now.plusDays(1).withHour(8).withMinute(0).toInstant()) }
    }

    @Test
    fun `snooze schedules nine minutes ahead in the snooze slot and keeps the alarm enabled`() = runBlocking {
        repository.upsert(oneShot("t", now.plusMinutes(1)))

        coordinator.onRingFinished("t", snoozed = true)

        verify { scheduler.scheduleSnooze("t", now.plusMinutes(9).toInstant()) }
        assertTrue(repository.get("t")!!.enabled)
    }
}
