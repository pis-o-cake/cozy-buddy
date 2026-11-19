package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.protocol.v1.AlarmControlAction
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.ErrorCode
import app.cozybuddy.protocol.v1.alarmControl
import app.cozybuddy.protocol.v1.envelope
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AlarmVoiceControlTest {

    private val link = FakeHubLink()
    private val holder = RingingStateHolder()
    private val control = mockk<AlarmRingControl>(relaxed = true)

    private fun TestScope.voiceControl(): AlarmVoiceControl =
        AlarmVoiceControl(link, holder, control, backgroundScope).also { it.start() }

    private fun TestScope.reporter() = HubStatusReporter(link, holder, backgroundScope).also { it.start() }

    private fun ring(id: String = "a1") = holder.start(RingingState.Ringing(id, AlarmKind.ALARM, "기상", 0L))

    private fun request(action: AlarmControlAction, id: String = "a1", minutes: Int = 0): Envelope = envelope {
        alarmControl = alarmControl {
            requestId = "r1"
            alarmId = id
            this.action = action
            snoozeMinutes = minutes
        }
    }

    private fun results() =
        link.sent.filter { it.bodyCase == Envelope.BodyCase.ALARM_CONTROL_RESULT }.map { it.alarmControlResult }

    private fun statuses() =
        link.sent.filter { it.bodyCase == Envelope.BodyCase.HUB_STATUS }.map { it.hubStatus.ringingAlarmId }

    @Test
    fun `stop request stops the ring and answers ok`() = runTest {
        voiceControl()
        ring()

        link.receive(request(AlarmControlAction.ALARM_CONTROL_ACTION_STOP))
        runCurrent()

        verify(exactly = 1) { control.stop() }
        val result = results().single()
        assertTrue(result.ok)
        assertEquals("r1", result.requestId)
        assertEquals("a1", result.alarmId)
    }

    @Test
    fun `snooze request without minutes uses the default of nine`() = runTest {
        voiceControl()
        ring()

        link.receive(request(AlarmControlAction.ALARM_CONTROL_ACTION_SNOOZE))
        runCurrent()

        verify(exactly = 1) { control.snooze(9) }
        assertTrue(results().single().ok)
    }

    @Test
    fun `snooze request passes the given minutes`() = runTest {
        voiceControl()
        ring()

        link.receive(request(AlarmControlAction.ALARM_CONTROL_ACTION_SNOOZE, minutes = 5))
        runCurrent()

        verify(exactly = 1) { control.snooze(5) }
    }

    @Test
    fun `snooze beyond the limit is rejected`() = runTest {
        voiceControl()
        ring()

        link.receive(request(AlarmControlAction.ALARM_CONTROL_ACTION_SNOOZE, minutes = 61))
        runCurrent()

        verify(exactly = 0) { control.snooze(any()) }
        val result = results().single()
        assertFalse(result.ok)
        assertEquals(ErrorCode.ERROR_CODE_BAD_REQUEST, result.code)
    }

    @Test
    fun `request while idle is answered not found`() = runTest {
        voiceControl()

        link.receive(request(AlarmControlAction.ALARM_CONTROL_ACTION_STOP))
        runCurrent()

        verify(exactly = 0) { control.stop() }
        val result = results().single()
        assertFalse(result.ok)
        assertEquals(ErrorCode.ERROR_CODE_NOT_FOUND, result.code)
    }

    @Test
    fun `request for another alarm is answered not found`() = runTest {
        voiceControl()
        ring("a1")

        link.receive(request(AlarmControlAction.ALARM_CONTROL_ACTION_STOP, id = "other"))
        runCurrent()

        verify(exactly = 0) { control.stop() }
        assertEquals(ErrorCode.ERROR_CODE_NOT_FOUND, results().single().code)
    }

    @Test
    fun `unspecified action is a bad request`() = runTest {
        voiceControl()
        ring()

        link.receive(request(AlarmControlAction.ALARM_CONTROL_ACTION_UNSPECIFIED))
        runCurrent()

        assertEquals(ErrorCode.ERROR_CODE_BAD_REQUEST, results().single().code)
    }

    @Test
    fun `status is sent on connect and on every ringing change`() = runTest {
        reporter()
        runCurrent()
        assertEquals(emptyList<String>(), statuses())

        link.connect()
        runCurrent()
        assertEquals(listOf(""), statuses())

        ring("a1")
        runCurrent()
        holder.clear()
        runCurrent()
        assertEquals(listOf("", "a1", ""), statuses())
    }

    @Test
    fun `status reports the alarm already ringing when the link connects`() = runTest {
        ring("a1")
        reporter()

        link.connect()
        runCurrent()

        assertEquals(listOf("a1"), statuses())
    }
}
