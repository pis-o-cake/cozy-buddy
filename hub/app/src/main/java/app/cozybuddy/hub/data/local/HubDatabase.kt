package app.cozybuddy.hub.data.local

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(
    entities = [AlarmEntity::class, HubRevisionEntity::class, SnoozeEntity::class],
    version = 2,
    exportSchema = true,
)
abstract class HubDatabase : RoomDatabase() {
    abstract fun alarmDao(): AlarmDao

    abstract fun snoozeDao(): SnoozeDao

    companion object {
        const val NAME = "hub.db"
    }
}
