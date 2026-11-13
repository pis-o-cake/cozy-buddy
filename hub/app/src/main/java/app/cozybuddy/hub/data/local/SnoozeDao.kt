package app.cozybuddy.hub.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface SnoozeDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: SnoozeEntity)

    @Query("DELETE FROM alarm_snoozes WHERE alarm_id = :alarmId")
    suspend fun delete(alarmId: String)

    @Query("SELECT * FROM alarm_snoozes ORDER BY fire_at_epoch_ms, alarm_id")
    suspend fun getAll(): List<SnoozeEntity>
}
