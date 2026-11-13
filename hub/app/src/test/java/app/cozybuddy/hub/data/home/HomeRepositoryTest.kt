package app.cozybuddy.hub.data.home

import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.protocol.v1.area
import app.cozybuddy.protocol.v1.commandResult
import app.cozybuddy.protocol.v1.entity
import app.cozybuddy.protocol.v1.entityState
import app.cozybuddy.protocol.v1.entityStateChanged
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.homeSnapshot
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class HomeRepositoryTest {

    private val link = FakeHubLink()

    private fun snapshot() = envelope {
        homeSnapshot = homeSnapshot {
            areas.add(area { areaId = "living"; name = "거실" })
            entities.add(
                entity {
                    entityId = "light.lamp"
                    areaId = "living"
                    name = "스탠드"
                    domain = "light"
                    state = entityState { state = "off"; changedEpochMs = 1 }
                },
            )
        }
    }

    @Test
    fun `snapshot fills areas and entities and state change updates one entity`() = runTest {
        val repository = HomeRepository(link, backgroundScope)
        repository.start()
        runCurrent()

        link.receive(snapshot())
        link.receive(
            envelope {
                entityStateChanged = entityStateChanged {
                    entityId = "light.lamp"
                    state = entityState { state = "on"; changedEpochMs = 2; attributes["brightness"] = "80" }
                }
            },
        )
        runCurrent()

        val state = repository.state.value
        assertEquals("거실", state.areas.single().name)
        val lamp = state.entities.getValue("light.lamp")
        assertEquals("on", lamp.state.state)
        assertEquals(mapOf("brightness" to "80"), lamp.state.attributes)
        assertEquals("스탠드", lamp.name)
    }

    @Test
    fun `unknown entity change and command results leave state untouched`() = runTest {
        val repository = HomeRepository(link, backgroundScope)
        repository.start()
        runCurrent()
        link.receive(snapshot())

        link.receive(envelope { entityStateChanged = entityStateChanged { entityId = "light.ghost" } })
        link.receive(envelope { commandResult = commandResult { requestId = "r"; ok = true } })
        runCurrent()

        assertEquals(setOf("light.lamp"), repository.state.value.entities.keys)
        assertEquals("off", repository.state.value.entities.getValue("light.lamp").state.state)
    }
}
