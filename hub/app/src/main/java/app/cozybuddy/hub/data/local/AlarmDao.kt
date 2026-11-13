package app.cozybuddy.hub.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

@Dao
abstract class AlarmDao {

    @Query("SELECT * FROM alarms ORDER BY hour IS NULL, hour, minute, fires_at_epoch_ms, alarm_id")
    abstract fun observeAll(): Flow<List<AlarmEntity>>

    @Query("SELECT * FROM alarms WHERE alarm_id = :id")
    abstract suspend fun get(id: String): AlarmEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    protected abstract suspend fun insert(entity: AlarmEntity)

    @Query("DELETE FROM alarms WHERE alarm_id = :id")
    protected abstract suspend fun deleteById(id: String): Int

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    protected abstract suspend fun insertRevisionIfAbsent(row: HubRevisionEntity)

    @Query("UPDATE hub_revision SET value = value + 1 WHERE id = ${HubRevisionEntity.SINGLETON_ID}")
    protected abstract suspend fun incrementRevision()

    @Query("SELECT value FROM hub_revision WHERE id = ${HubRevisionEntity.SINGLETON_ID}")
    abstract suspend fun currentRevision(): Long?

    /** 같은 트랜잭션에서 revision 을 올리고 그 값으로 행을 저장한다. */
    @Transaction
    open suspend fun upsertWithNewRevision(entity: AlarmEntity, nowEpochMs: Long): AlarmEntity {
        val revision = bumpRevision()
        val stored = entity.copy(revision = revision, updatedAt = nowEpochMs)
        insert(stored)
        return stored
    }

    @Transaction
    open suspend fun deleteWithNewRevision(id: String): Boolean {
        if (deleteById(id) == 0) return false
        bumpRevision()
        return true
    }

    private suspend fun bumpRevision(): Long {
        insertRevisionIfAbsent(HubRevisionEntity(value = 0L))
        incrementRevision()
        return checkNotNull(currentRevision()) { "hub revision row missing" }
    }
}
