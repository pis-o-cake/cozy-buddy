package app.cozybuddy.hub.data.home

import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.di.ApplicationScope
import app.cozybuddy.hub.domain.model.HomeArea
import app.cozybuddy.hub.domain.model.HomeEntity
import app.cozybuddy.hub.domain.model.HomeEntityState
import app.cozybuddy.hub.domain.model.HomeState
import app.cozybuddy.protocol.v1.Envelope
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import timber.log.Timber
import app.cozybuddy.protocol.v1.EntityState as EntityStateProto

/**
 * 서버가 내려주는 구역·기기 상태의 메모리 사본. 아직 화면은 없고 대시보드가 구독할 자리다.
 *
 * 영속하지 않는다: 연결할 때마다 HomeSnapshot 이 전체를 다시 채운다.
 */
@Singleton
class HomeRepository @Inject constructor(
    private val link: HubLink,
    @ApplicationScope private val scope: CoroutineScope,
) {
    private val _state = MutableStateFlow(HomeState())
    val state: StateFlow<HomeState> = _state.asStateFlow()

    /** 수신 구독을 즉시 등록한다. */
    fun start() {
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            link.inbound.collect { message -> handle(message) }
        }
    }

    internal fun handle(message: Envelope) {
        when (message.bodyCase) {
            Envelope.BodyCase.HOME_SNAPSHOT -> {
                val snapshot = message.homeSnapshot
                _state.value = HomeState(
                    areas = snapshot.areasList.map { HomeArea(it.areaId, it.name) },
                    entities = snapshot.entitiesList.map { entity ->
                        HomeEntity(
                            id = entity.entityId,
                            areaId = entity.areaId,
                            name = entity.name,
                            domain = entity.domain,
                            state = entity.state.toDomain(),
                        )
                    }.associateBy { it.id },
                )
                Timber.i("Home snapshot: %d areas, %d entities", _state.value.areas.size, _state.value.entities.size)
            }
            Envelope.BodyCase.ENTITY_STATE_CHANGED -> {
                val changed = message.entityStateChanged
                _state.update { current ->
                    val entity = current.entities[changed.entityId]
                    if (entity == null) {
                        Timber.w("State change for unknown entity %s", changed.entityId)
                        return@update current
                    }
                    current.copy(
                        entities = current.entities + (entity.id to entity.copy(state = changed.state.toDomain())),
                    )
                }
            }
            Envelope.BodyCase.COMMAND_RESULT -> {
                val result = message.commandResult
                Timber.i("Command result request=%s ok=%b code=%s", result.requestId, result.ok, result.code)
            }
            else -> Unit
        }
    }

    private fun EntityStateProto.toDomain() = HomeEntityState(state, attributesMap, changedEpochMs)
}
