package app.cozybuddy.hub.data.local

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

// WARNING: alarms 에 FK(CASCADE)를 걸지 않는다. 알람 저장이 REPLACE 라 부모 행이 삭제 후 재삽입되며 스누즈가 같이 지워진다.
@Entity(tableName = "alarm_snoozes")
data class SnoozeEntity(
    @PrimaryKey @ColumnInfo(name = "alarm_id") val alarmId: String,
    @ColumnInfo(name = "fire_at_epoch_ms") val fireAtEpochMs: Long,
)
