package app.cozybuddy.hub.data.local

import android.content.ContentValues
import android.database.sqlite.SQLiteDatabase
import androidx.room.testing.MigrationTestHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class HubMigrationTest {

    @get:Rule
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(),
        HubDatabase::class.java,
        emptyList(),
        FrameworkSQLiteOpenHelperFactory(),
    )

    @Test
    fun `migrating 1 to 2 keeps alarms and adds an empty snooze table`() {
        helper.createDatabase(TEST_DB, 1).apply {
            insert(
                "alarms",
                SQLiteDatabase.CONFLICT_NONE,
                ContentValues().apply {
                    put("alarm_id", "a")
                    put("kind", "ALARM")
                    put("label", "wake")
                    put("hour", 7)
                    put("minute", 30)
                    put("days_mask", 31)
                    put("enabled", 1)
                    put("sunrise", 0)
                    put("revision", 4)
                    put("updated_at", 100)
                },
            )
            close()
        }

        val migrated = helper.runMigrationsAndValidate(TEST_DB, 2, true, HubMigrations.MIGRATION_1_2)

        migrated.query("SELECT label, revision FROM alarms WHERE alarm_id = 'a'").use {
            it.moveToFirst()
            assertEquals("wake", it.getString(0))
            assertEquals(4L, it.getLong(1))
        }
        migrated.query("SELECT COUNT(*) FROM alarm_snoozes").use {
            it.moveToFirst()
            assertEquals(0, it.getInt(0))
        }
    }

    private companion object {
        const val TEST_DB = "migration-test.db"
    }
}
