package app.cozybuddy.hub.data.local

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "alarms")
data class AlarmEntity(
    @PrimaryKey @ColumnInfo(name = "alarm_id") val alarmId: String,
    val kind: String,
    val label: String,
    @ColumnInfo(name = "fires_at_epoch_ms") val firesAtEpochMs: Long?,
    val hour: Int?,
    val minute: Int?,
    // 비트 n = ISO 요일 n+1 (월=bit0 ... 일=bit6).
    @ColumnInfo(name = "days_mask") val daysMask: Int,
    val enabled: Boolean,
    val sunrise: Boolean,
    val revision: Long,
    @ColumnInfo(name = "updated_at") val updatedAt: Long,
)

/** 허브 전역 revision 카운터. 항상 id=1 한 행만 둔다. */
@Entity(tableName = "hub_revision")
data class HubRevisionEntity(
    @PrimaryKey val id: Int = SINGLETON_ID,
    val value: Long,
) {
    companion object {
        const val SINGLETON_ID = 1
    }
}
