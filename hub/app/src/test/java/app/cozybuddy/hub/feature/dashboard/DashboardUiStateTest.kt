package app.cozybuddy.hub.feature.dashboard

import app.cozybuddy.hub.domain.model.HomeArea
import app.cozybuddy.hub.domain.model.HomeEntity
import app.cozybuddy.hub.domain.model.HomeEntityState
import app.cozybuddy.hub.domain.model.HomeState
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test

class DashboardUiStateTest {

    private fun entity(
        id: String,
        area: String,
        name: String = id,
        state: String = "off",
        attributes: Map<String, String> = emptyMap(),
    ) = HomeEntity(id, area, name, id.substringBefore('.'), HomeEntityState(state, attributes, 0))

    private fun home(vararg entities: HomeEntity, areas: List<HomeArea>) =
        HomeState(areas, entities.associateBy { it.id })

    private val areas = listOf(HomeArea("kitchen", "주방"), HomeArea("living", "거실"), HomeArea("bed", "침실"))

    @Test
    fun `hub area comes first then others alphabetically then the other group`() {
        val state = home(
            entity("light.a", "kitchen"),
            entity("light.b", "living"),
            entity("light.c", "bed"),
            entity("sensor.orphan", ""),
            entity("sensor.ghost", "removed-area"),
            areas = areas,
        )

        val sections = buildSections(state, hubAreaId = "kitchen", pending = emptySet())

        // 이름순: 거실(living) < 주방(kitchen) < 침실(bed), 허브 구역(kitchen)이 맨 앞.
        assertEquals(listOf("kitchen", "living", "bed", null), sections.map { it.areaId })
        assertEquals(listOf("sensor.ghost", "sensor.orphan"), sections.last().cards.map { it.id })
    }

    @Test
    fun `without a known hub area areas are alphabetical and empty areas are hidden`() {
        val state = home(entity("light.a", "kitchen"), entity("light.b", "bed"), areas = areas)

        val sections = buildSections(state, hubAreaId = null, pending = emptySet())

        assertEquals(listOf("kitchen", "bed"), sections.map { it.areaId })
    }

    @Test
    fun `cards are sorted by name and carry pending`() {
        val state = home(
            entity("light.z", "living", name = "Zeta"),
            entity("light.a", "living", name = "alpha"),
            areas = areas,
        )

        val cards = buildSections(state, "living", pending = setOf("light.z")).single().cards

        assertEquals(listOf("alpha", "Zeta"), cards.map { it.name })
        assertEquals(listOf(false, true), cards.map { it.pending })
    }

    @Test
    fun `domains map to card kinds and states`() {
        fun card(e: HomeEntity) = e.toCard(pending = false)

        assertEquals(CardKind.LIGHT, card(entity("light.x", "a")).kind)
        assertEquals(CardKind.TOGGLE, card(entity("fan.x", "a")).kind)
        assertEquals(CardKind.TOGGLE, card(entity("climate.x", "a")).kind)
        assertEquals(CardKind.COVER, card(entity("cover.x", "a")).kind)
        assertEquals(CardKind.SCENE, card(entity("scene.x", "a", state = "2026-01-01T00:00:00")).kind)
        assertEquals(CardKind.SCRIPT, card(entity("script.x", "a")).kind)
        assertEquals(CardKind.READ_ONLY, card(entity("lock.x", "a", state = "locked")).kind)
        assertEquals(CardState.LOCKED, card(entity("lock.x", "a", state = "locked")).state)
        assertEquals(CardState.OPEN, card(entity("cover.x", "a", state = "open")).state)
        assertEquals(CardState.CLOSED, card(entity("cover.x", "a", state = "closed")).state)
        assertEquals(CardState.UNAVAILABLE, card(entity("light.x", "a", state = "unavailable")).state)

        val sensor = card(entity("sensor.t", "a", state = "21.5", attributes = mapOf("unit_of_measurement" to "°C")))
        assertEquals(CardState.VALUE, sensor.state)
        assertEquals("21.5", sensor.value)
        assertEquals("°C", sensor.unit)
    }

    @Test
    fun `light brightness converts from 0-255 to percent only while on`() {
        val on = entity("light.x", "a", state = "on", attributes = mapOf("brightness" to "128")).toCard(false)
        val off = entity("light.x", "a", state = "off", attributes = mapOf("brightness" to "128")).toCard(false)
        val plain = entity("light.x", "a", state = "on").toCard(false)

        assertEquals(50, on.brightnessPct)
        assertNull(off.brightnessPct)
        assertNull(plain.brightnessPct)
    }
}
