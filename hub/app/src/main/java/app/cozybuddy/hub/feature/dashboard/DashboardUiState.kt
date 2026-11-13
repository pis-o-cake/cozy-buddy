package app.cozybuddy.hub.feature.dashboard

import app.cozybuddy.hub.data.home.CommandFailure
import app.cozybuddy.hub.domain.model.HomeEntity
import app.cozybuddy.hub.domain.model.HomeState
import kotlin.math.roundToInt

enum class CardKind { LIGHT, TOGGLE, COVER, SCENE, SCRIPT, READ_ONLY }

/** 카드에 보일 상태. 문구는 화면이 리소스로 푼다. */
enum class CardState { ON, OFF, OPEN, CLOSED, LOCKED, UNLOCKED, UNAVAILABLE, VALUE }

data class EntityCard(
    val id: String,
    val name: String,
    val kind: CardKind,
    val state: CardState,
    /** [CardState.VALUE] 일 때 보여줄 원문 값. */
    val value: String = "",
    val unit: String = "",
    val brightnessPct: Int? = null,
    val pending: Boolean = false,
)

/** 한 구역의 카드 묶음. [areaId] 가 null 이면 구역이 없는 기기의 "기타" 묶음이다. */
data class AreaSection(val areaId: String?, val name: String, val cards: List<EntityCard>)

enum class DashboardConnection { CONNECTED, OFFLINE, AUTH_FAILED }

data class DashboardUiState(
    val connection: DashboardConnection = DashboardConnection.OFFLINE,
    val sections: List<AreaSection> = emptyList(),
    val error: CommandFailure? = null,
) {
    val controlsEnabled: Boolean get() = connection == DashboardConnection.CONNECTED
}

private val TOGGLE_DOMAINS = setOf("switch", "fan", "media_player", "climate")
private const val HA_UNAVAILABLE = "unavailable"
private const val HA_UNKNOWN = "unknown"
private const val BRIGHTNESS_MAX = 255

/**
 * 구역별 카드 목록을 만든다. 허브가 놓인 구역이 맨 앞, 나머지는 이름순, 구역 없는 기기는 마지막 "기타".
 * 구역 안 카드는 이름순이다.
 */
internal fun buildSections(home: HomeState, hubAreaId: String?, pending: Set<String>): List<AreaSection> {
    val knownAreas = home.areas.associateBy { it.id }
    val grouped = home.entities.values.groupBy { if (it.areaId in knownAreas) it.areaId else null }

    val named = grouped.keys.filterNotNull()
        .sortedWith(
            compareBy<String> { if (it == hubAreaId) 0 else 1 }
                .thenBy(String.CASE_INSENSITIVE_ORDER) { knownAreas.getValue(it).name },
        )
        .map { id -> AreaSection(id, knownAreas.getValue(id).name, cardsOf(grouped.getValue(id), pending)) }
    val other = grouped[null]?.let { AreaSection(null, "", cardsOf(it, pending)) }
    return named + listOfNotNull(other)
}

private fun cardsOf(entities: List<HomeEntity>, pending: Set<String>): List<EntityCard> =
    entities.sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.name }).map { it.toCard(it.id in pending) }

internal fun HomeEntity.toCard(pending: Boolean): EntityCard {
    val raw = state.state
    val kind = when (domain) {
        "light" -> CardKind.LIGHT
        in TOGGLE_DOMAINS -> CardKind.TOGGLE
        "cover" -> CardKind.COVER
        "scene" -> CardKind.SCENE
        "script" -> CardKind.SCRIPT
        else -> CardKind.READ_ONLY
    }
    val cardState = when {
        raw == HA_UNAVAILABLE || raw == HA_UNKNOWN -> CardState.UNAVAILABLE
        kind == CardKind.SCENE || kind == CardKind.SCRIPT -> CardState.OFF
        domain == "lock" -> if (raw == "locked") CardState.LOCKED else CardState.UNLOCKED
        domain == "sensor" -> CardState.VALUE
        kind == CardKind.COVER -> if (raw == "open" || raw == "opening") CardState.OPEN else CardState.CLOSED
        raw == "on" -> CardState.ON
        raw == "off" -> CardState.OFF
        else -> CardState.VALUE
    }
    val brightness = if (kind == CardKind.LIGHT && cardState == CardState.ON) {
        state.attributes["brightness"]?.toIntOrNull()?.let { (it * 100f / BRIGHTNESS_MAX).roundToInt().coerceIn(0, 100) }
    } else {
        null
    }
    return EntityCard(
        id = id,
        name = name,
        kind = kind,
        state = cardState,
        value = raw,
        unit = state.attributes["unit_of_measurement"].orEmpty(),
        brightnessPct = brightness,
        pending = pending,
    )
}
