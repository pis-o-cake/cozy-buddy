package app.cozybuddy.hub.data.repository

import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.local.AlarmDao
import app.cozybuddy.hub.data.local.toDomain
import app.cozybuddy.hub.data.local.toEntity
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.repository.AlarmRepository
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import timber.log.Timber

@Singleton
class AlarmRepositoryImpl @Inject constructor(
    private val dao: AlarmDao,
    private val timeSource: TimeSource,
) : AlarmRepository {

    override fun observeAll(): Flow<List<Alarm>> =
        dao.observeAll().map { rows -> rows.map { it.toDomain() } }

    override suspend fun get(id: String): Alarm? = dao.get(id)?.toDomain()

    override suspend fun upsert(alarm: Alarm): Alarm {
        val now = timeSource.now().toInstant().toEpochMilli()
        val stored = dao.upsertWithNewRevision(alarm.toEntity(), now).toDomain()
        Timber.d("Alarm upserted id=%s revision=%d", stored.id, stored.revision)
        return stored
    }

    override suspend fun delete(id: String): Boolean {
        val deleted = dao.deleteWithNewRevision(id)
        Timber.d("Alarm delete id=%s deleted=%b", id, deleted)
        return deleted
    }

    override suspend fun currentRevision(): Long = dao.currentRevision() ?: 0L
}
