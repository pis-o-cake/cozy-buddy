package app.cozybuddy.hub.data.local

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

object HubMigrations {

    /** 스누즈 상태 테이블 추가. 기존 데이터는 건드리지 않는다. */
    val MIGRATION_1_2: Migration = object : Migration(1, 2) {
        override fun migrate(db: SupportSQLiteDatabase) {
            db.execSQL(
                "CREATE TABLE IF NOT EXISTS `alarm_snoozes` (" +
                    "`alarm_id` TEXT NOT NULL, " +
                    "`fire_at_epoch_ms` INTEGER NOT NULL, " +
                    "PRIMARY KEY(`alarm_id`))",
            )
        }
    }

    val ALL: Array<Migration> = arrayOf(MIGRATION_1_2)
}
