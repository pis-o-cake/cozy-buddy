package app.cozybuddy.hub.data.home

import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.commandResult
import app.cozybuddy.protocol.v1.envelope
import kotlinx.coroutines.async
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class LinkDeviceCommandSenderTest {

    private val link = FakeHubLink()
    private val sender = LinkDeviceCommandSender(link)

    private fun result(requestId: String, ok: Boolean, key: String = ""): Envelope = envelope {
        commandResult = commandResult { this.requestId = requestId; this.ok = ok; messageKey = key }
    }

    @Test
    fun `sends command with params and resolves on the matching result only`() = runTest {
        val outcome = async { sender.send("light.lamp", "set_brightness", mapOf("brightness_pct" to "40")) }
        runCurrent()

        val command = link.sent.single().entityCommand
        assertEquals("light.lamp", command.entityId)
        assertEquals("set_brightness", command.action)
        assertEquals("40", command.paramsMap["brightness_pct"])
        assertTrue(command.requestId.isNotBlank())

        link.receive(result("someone-else", ok = false, key = "home.unavailable"))
        runCurrent()
        assertFalse(outcome.isCompleted)

        link.receive(result(command.requestId, ok = true))
        runCurrent()
        assertEquals(CommandOutcome.Success, outcome.await())
    }

    @Test
    fun `request ids differ between commands`() = runTest {
        val first = async { sender.send("light.a", "turn_on") }
        val second = async { sender.send("light.b", "turn_on") }
        runCurrent()

        val ids = link.sent.map { it.entityCommand.requestId }
        assertEquals(2, ids.toSet().size)
        ids.forEach { link.receive(result(it, ok = true)) }
        runCurrent()
        assertEquals(CommandOutcome.Success, first.await())
        assertEquals(CommandOutcome.Success, second.await())
    }

    @Test
    fun `times out after five seconds without a result`() = runTest {
        val outcome = async { sender.send("light.lamp", "turn_on") }
        runCurrent()

        advanceTimeBy(LinkDeviceCommandSender.TIMEOUT_MS - 1)
        runCurrent()
        assertFalse(outcome.isCompleted)

        advanceTimeBy(2)
        runCurrent()
        assertEquals(CommandOutcome.Failure(CommandFailure.TIMEOUT), outcome.await())
    }

    @Test
    fun `reports not connected when the link refuses the frame`() = runTest {
        link.accepting = false
        assertEquals(CommandOutcome.Failure(CommandFailure.NOT_CONNECTED), sender.send("light.lamp", "turn_on"))
    }

    @Test
    fun `maps message keys to failures with a generic fallback`() = runTest {
        val expected = mapOf(
            "home.unavailable" to CommandFailure.UNAVAILABLE,
            "home.unsupported_action" to CommandFailure.UNSUPPORTED_ACTION,
            "home.entity_not_found" to CommandFailure.ENTITY_NOT_FOUND,
            "home.something_new" to CommandFailure.GENERIC,
            "" to CommandFailure.GENERIC,
        )
        expected.forEach { (key, failure) ->
            link.sent.clear()
            val outcome = async { sender.send("light.lamp", "turn_on") }
            runCurrent()
            link.receive(result(link.sent.single().entityCommand.requestId, ok = false, key = key))
            runCurrent()
            assertEquals(CommandOutcome.Failure(failure), outcome.await(), key)
        }
    }
}
