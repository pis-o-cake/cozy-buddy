package app.cozybuddy.hub.data.sync

import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.protocol.toDomain
import app.cozybuddy.hub.data.protocol.toProto
import app.cozybuddy.hub.di.ApplicationScope
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.hub.domain.repository.AlarmRepository
import app.cozybuddy.hub.domain.usecase.AlarmCoordinator
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.ErrorCode
import app.cozybuddy.protocol.v1.alarmAck
import app.cozybuddy.protocol.v1.alarmSnapshot
import app.cozybuddy.protocol.v1.envelope
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import timber.log.Timber

/**
 * 허브(정본)와 서버 사본 사이의 알람 동기화.
 *
 * - 서버의 AlarmSyncRequest 에는 전체 목록(AlarmSnapshot)으로 답한다.
 * - 서버의 AlarmUpsert/AlarmDelete 는 [AlarmCoordinator] 로 적용하고, 허브가 새로 매긴 revision 을 AlarmAck 로 돌려준다.
 * - 연결된 동안 로컬에서 알람이 바뀌면 새 AlarmSnapshot 을 보낸다.
 */
@Singleton
@OptIn(FlowPreview::class)
class AlarmSyncManager @Inject constructor(
    private val link: HubLink,
    private val repository: AlarmRepository,
    private val coordinator: AlarmCoordinator,
    private val settings: ServerSettings,
    private val timeSource: TimeSource,
    @ApplicationScope private val scope: CoroutineScope,
) {
    private val mutex = Mutex()

    // 서버가 알고 있다고 믿는 상태(alarm_id -> revision). 연결 밖이면 null.
    // 서버가 시작한 변경이 로컬 변경 흐름으로 되돌아와 스냅샷을 다시 보내는(에코) 것을 막는 기준이다.
    private var published: Map<String, Long>? = null

    /** 수신 구독을 즉시 등록한다. 연결을 시작하기 전에 호출해야 첫 AlarmSyncRequest 를 놓치지 않는다. */
    fun start() {
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            link.inbound.collect { message -> handle(message) }
        }
        scope.launch { observeLocalChanges() }
    }

    private suspend fun observeLocalChanges() {
        link.state.map { it is ConnectionState.Connected }.distinctUntilChanged().collectLatest { connected ->
            if (!connected) {
                mutex.withLock { published = null }
                return@collectLatest
            }
            // 연결 직후에는 서버가 AlarmSyncRequest 로 전체 목록을 가져가므로 현재 상태를 기준선으로 삼는다.
            mutex.withLock { published = fingerprint(repository.observeAll().first()) }
            repository.observeAll().debounce(LOCAL_CHANGE_DEBOUNCE_MS).collect { alarms -> publishIfChanged(alarms) }
        }
    }

    private suspend fun handle(message: Envelope) {
        try {
            when (message.bodyCase) {
                Envelope.BodyCase.ALARM_SYNC_REQUEST -> publishSnapshot()
                Envelope.BodyCase.ALARM_UPSERT -> applyUpsert(message)
                Envelope.BodyCase.ALARM_DELETE -> applyDelete(message)
                else -> Unit
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Timber.e(e, "Alarm sync handling failed: %s", message.bodyCase)
        }
    }

    private suspend fun publishSnapshot() {
        val alarms = repository.observeAll().first()
        mutex.withLock { send(alarms) }
    }

    private suspend fun publishIfChanged(alarms: List<Alarm>) {
        mutex.withLock {
            val current = published ?: return
            if (fingerprint(alarms) == current) return
            send(alarms)
        }
    }

    // mutex 안에서만 호출한다.
    private suspend fun send(alarms: List<Alarm>) {
        val hubId = settings.config.first().clientId
        val snapshot = envelope {
            alarmSnapshot = alarmSnapshot {
                this.hubId = hubId
                this.alarms.addAll(alarms.map { it.toProto(hubId) })
            }
        }
        if (!link.send(snapshot)) {
            Timber.w("Alarm snapshot not sent: link is down")
            return
        }
        published = fingerprint(alarms)
        Timber.i("Alarm snapshot sent: %d alarms", alarms.size)
    }

    private suspend fun applyUpsert(message: Envelope) {
        val upsert = message.alarmUpsert
        val ack = try {
            val incoming = upsert.alarm.toDomain(updatedAtEpochMs = timeSource.now().toInstant().toEpochMilli())
            validate(incoming)
            val stored = coordinator.save(incoming)
            mutex.withLock { published = published?.plus(stored.id to stored.revision) }
            ackOf(upsert.requestId, stored.id, stored.revision)
        } catch (e: IllegalArgumentException) {
            Timber.w(e, "Rejected alarm upsert request=%s", upsert.requestId)
            failedAck(upsert.requestId, upsert.alarm.alarmId, ErrorCode.ERROR_CODE_BAD_REQUEST)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Timber.e(e, "Failed to apply alarm upsert request=%s", upsert.requestId)
            failedAck(upsert.requestId, upsert.alarm.alarmId, ErrorCode.ERROR_CODE_INTERNAL)
        }
        link.send(ack)
    }

    private suspend fun applyDelete(message: Envelope) {
        val delete = message.alarmDelete
        val ack = try {
            require(delete.alarmId.isNotBlank()) { "alarm_id is blank" }
            // 이미 없는 알람의 삭제는 멱등으로 성공 처리해야 서버 사본이 pending_delete 에서 풀린다.
            coordinator.delete(delete.alarmId)
            mutex.withLock { published = published?.minus(delete.alarmId) }
            ackOf(delete.requestId, delete.alarmId, repository.currentRevision())
        } catch (e: IllegalArgumentException) {
            Timber.w(e, "Rejected alarm delete request=%s", delete.requestId)
            failedAck(delete.requestId, delete.alarmId, ErrorCode.ERROR_CODE_BAD_REQUEST)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Timber.e(e, "Failed to apply alarm delete request=%s", delete.requestId)
            failedAck(delete.requestId, delete.alarmId, ErrorCode.ERROR_CODE_INTERNAL)
        }
        link.send(ack)
    }

    private fun validate(alarm: Alarm) {
        require(alarm.id.isNotBlank()) { "alarm_id is blank" }
        val schedule = alarm.schedule as? AlarmSchedule.Repeating ?: return
        require(schedule.hour in 0..MAX_HOUR && schedule.minute in 0..MAX_MINUTE) {
            "Invalid time ${schedule.hour}:${schedule.minute}"
        }
    }

    private fun ackOf(requestId: String, alarmId: String, revision: Long): Envelope = envelope {
        alarmAck = alarmAck {
            this.requestId = requestId
            this.alarmId = alarmId
            this.revision = revision
            ok = true
        }
    }

    private fun failedAck(requestId: String, alarmId: String, code: ErrorCode): Envelope = envelope {
        alarmAck = alarmAck {
            this.requestId = requestId
            this.alarmId = alarmId
            ok = false
            this.code = code
        }
    }

    private fun fingerprint(alarms: List<Alarm>): Map<String, Long> = alarms.associate { it.id to it.revision }

    private companion object {
        const val LOCAL_CHANGE_DEBOUNCE_MS = 300L
        const val MAX_HOUR = 23
        const val MAX_MINUTE = 59
    }
}
