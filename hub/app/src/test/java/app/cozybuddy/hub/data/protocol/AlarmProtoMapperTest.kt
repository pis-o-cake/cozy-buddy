package app.cozybuddy.hub.data.protocol

import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.protocol.v1.alarm
import java.time.DayOfWeek
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import app.cozybuddy.protocol.v1.Alarm as AlarmProto

class AlarmProtoMapperTest {

    private val repeating = Alarm(
        id = "a-1",
        kind = AlarmKind.ALARM,
        label = "출근",
        schedule = AlarmSchedule.Repeating(7, 30, setOf(DayOfWeek.MONDAY, DayOfWeek.FRIDAY, DayOfWeek.SUNDAY)),
        enabled = true,
        sunrise = true,
        revision = 42,
        updatedAtEpochMs = 1_000,
    )

    private val timer = Alarm(
        id = "t-1",
        kind = AlarmKind.TIMER,
        label = "",
        schedule = AlarmSchedule.OneShot(1_700_000_000_000),
        enabled = false,
        revision = 7,
        updatedAtEpochMs = 2_000,
    )

    @Test
    fun `repeating alarm survives domain to proto to bytes to domain`() {
        val proto = repeating.toProto("hub-1")
        val parsed = AlarmProto.parseFrom(proto.toByteArray())

        assertEquals("hub-1", parsed.hubId)
        assertEquals(repeating, parsed.toDomain(updatedAtEpochMs = 1_000))
    }

    @Test
    fun `one-shot timer survives round trip`() {
        val parsed = AlarmProto.parseFrom(timer.toProto("hub-1").toByteArray())

        assertEquals(timer, parsed.toDomain(updatedAtEpochMs = 2_000))
    }

    @Test
    fun `proto day numbers follow ISO order`() {
        val proto = repeating.toProto("hub-1")

        assertEquals(listOf(1, 5, 7), proto.repeating.daysList.map { it.number })
    }

    @Test
    fun `missing schedule is rejected`() {
        val proto = alarm { alarmId = "x" }

        assertThrows(IllegalArgumentException::class.java) { proto.toDomain(0) }
    }
}
