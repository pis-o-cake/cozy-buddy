package app.cozybuddy.hub.data.local

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import app.cozybuddy.hub.data.repository.SnoozeRepositoryImpl
import app.cozybuddy.hub.domain.model.Snooze
import java.time.Instant
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SnoozeDaoTest {

    private lateinit var db: HubDatabase
    private lateinit var repository: SnoozeRepositoryImpl

    @Before
    fun setUp() {
        val context: Context = ApplicationProvider.getApplicationContext()
        db = Room.inMemoryDatabaseBuilder(context, HubDatabase::class.java).allowMainThreadQueries().build()
        repository = SnoozeRepositoryImpl(db.snoozeDao())
    }

    @After
    fun tearDown() = db.close()

    @Test
    fun `put replaces the existing snooze of the same alarm`() = runBlocking {
        repository.put(Snooze("a", Instant.ofEpochMilli(1_000)))
        repository.put(Snooze("a", Instant.ofEpochMilli(2_000)))

        assertEquals(listOf(Snooze("a", Instant.ofEpochMilli(2_000))), repository.getAll())
    }

    @Test
    fun `clear removes only the given alarm`() = runBlocking {
        repository.put(Snooze("a", Instant.ofEpochMilli(1_000)))
        repository.put(Snooze("b", Instant.ofEpochMilli(2_000)))

        repository.clear("a")

        assertEquals(listOf("b"), repository.getAll().map { it.alarmId })
    }

    @Test
    fun `snooze survives reopening the database`() = runBlocking {
        val context: Context = ApplicationProvider.getApplicationContext()
        val name = "snooze-persist.db"
        context.deleteDatabase(name)
        var file = Room.databaseBuilder(context, HubDatabase::class.java, name).allowMainThreadQueries().build()
        SnoozeRepositoryImpl(file.snoozeDao()).put(Snooze("a", Instant.ofEpochMilli(5_000)))
        file.close()

        file = Room.databaseBuilder(context, HubDatabase::class.java, name).allowMainThreadQueries().build()
        val restored = SnoozeRepositoryImpl(file.snoozeDao()).getAll()
        file.close()
        context.deleteDatabase(name)

        assertEquals(listOf(Snooze("a", Instant.ofEpochMilli(5_000))), restored)
    }

    @Test
    fun `replacing an alarm row does not drop its snooze`() = runBlocking {
        val alarmDao = db.alarmDao()
        val entity = AlarmEntity("a", "ALARM", "", null, 7, 0, 0, true, false, 0, 0)
        alarmDao.upsertWithNewRevision(entity, 1)
        repository.put(Snooze("a", Instant.ofEpochMilli(1_000)))

        alarmDao.upsertWithNewRevision(entity.copy(label = "x"), 2)

        assertTrue(repository.getAll().isNotEmpty())
    }
}
