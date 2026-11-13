package app.cozybuddy.hub.domain.model

data class HomeArea(val id: String, val name: String)

data class HomeEntityState(
    val state: String,
    val attributes: Map<String, String>,
    val changedAtEpochMs: Long,
)

/** 집 기기 한 건. [domain] 은 light, switch, climate 같은 HA 도메인이다. */
data class HomeEntity(
    val id: String,
    val areaId: String,
    val name: String,
    val domain: String,
    val state: HomeEntityState,
)

data class HomeState(
    val areas: List<HomeArea> = emptyList(),
    val entities: Map<String, HomeEntity> = emptyMap(),
)
