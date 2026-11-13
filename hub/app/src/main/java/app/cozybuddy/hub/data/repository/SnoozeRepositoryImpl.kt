package app.cozybuddy.hub.data.repository

import app.cozybuddy.hub.data.local.SnoozeDao
import app.cozybuddy.hub.data.local.SnoozeEntity
import app.cozybuddy.hub.domain.model.Snooze
import app.cozybuddy.hub.domain.repository.SnoozeRepository
import java.time.Instant
import javax.inject.Inject

class SnoozeRepositoryImpl @Inject constructor(
    private val dao: SnoozeDao,
) : SnoozeRepository {

    override suspend fun put(snooze: Snooze) =
        dao.upsert(SnoozeEntity(snooze.alarmId, snooze.fireAt.toEpochMilli()))

    override suspend fun clear(alarmId: String) = dao.delete(alarmId)

    override suspend fun getAll(): List<Snooze> =
        dao.getAll().map { Snooze(it.alarmId, Instant.ofEpochMilli(it.fireAtEpochMs)) }
}
