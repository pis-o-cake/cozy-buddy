package app.cozybuddy.hub.data.local

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(
    entities = [AlarmEntity::class, HubRevisionEntity::class],
    version = 1,
    exportSchema = true,
)
abstract class HubDatabase : RoomDatabase() {
    abstract fun alarmDao(): AlarmDao

    companion object {
        const val NAME = "hub.db"
    }
}
