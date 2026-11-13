package app.cozybuddy.hub.data.repository

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.local.HubDatabase
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.model.AlarmSchedule
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Room DAO 는 JVM 단독으로 돌릴 수 없어 Robolectric(JUnit4, vintage 엔진)을 쓴다. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class AlarmRepositoryRevisionTest {

    private val context: Context = ApplicationProvider.getApplicationContext()
    private val dbName = "revision-test.db"
    private val timeSource = object : TimeSource {
        override fun now(): ZonedDateTime = ZonedDateTime.of(2026, 11, 13, 9, 0, 0, 0, ZoneId.of("Asia/Seoul"))
    }
    private var db: HubDatabase = open()

    private fun open(): HubDatabase =
        Room.databaseBuilder(context, HubDatabase::class.java, dbName).allowMainThreadQueries().build()

    private fun repository() = AlarmRepositoryImpl(db.alarmDao(), timeSource)

    private fun alarm(id: String) = Alarm(
        id = id,
        kind = AlarmKind.ALARM,
        label = id,
        schedule = AlarmSchedule.Repeating(7, 0, emptySet()),
        enabled = true,
    )

    @Before
    fun setUp() {
        context.deleteDatabase(dbName)
        db.close()
        db = open()
    }

    @After
    fun tearDown() {
        db.close()
        context.deleteDatabase(dbName)
    }

    @Test
    fun `revision increases on every upsert and delete`() = runBlocking {
        val repo = repository()

        val first = repo.upsert(alarm("a"))
        val second = repo.upsert(alarm("b"))
        val updatedFirst = repo.upsert(first.copy(label = "changed"))
        repo.delete("b")
        val afterDelete = repo.upsert(alarm("c"))

        assertEquals(listOf(1L, 2L, 3L, 5L), listOf(first, second, updatedFirst, afterDelete).map { it.revision })
        assertEquals(timeSource.now().toInstant().toEpochMilli(), first.updatedAtEpochMs)
    }

    @Test
    fun `revision never decreases after reopening the database`() = runBlocking {
        repository().upsert(alarm("a"))
        val beforeClose = repository().upsert(alarm("b"))
        db.close()

        db = open()
        val repo = repository()
        val afterReload = repo.upsert(alarm("c"))

        assertTrue(afterReload.revision > beforeClose.revision)
        assertEquals(beforeClose.revision + 1, afterReload.revision)
        assertEquals(3, repo.observeAll().first().size)
    }

    @Test
    fun `deleting a missing alarm keeps the revision`() = runBlocking {
        val repo = repository()
        val stored = repo.upsert(alarm("a"))

        assertFalse(repo.delete("missing"))

        assertEquals(stored.revision + 1, repo.upsert(alarm("b")).revision)
    }

    @Test
    fun `stored alarm round-trips through the entity mapping`() = runBlocking {
        val repo = repository()
        val oneShot = alarm("t").copy(kind = AlarmKind.TIMER, schedule = AlarmSchedule.OneShot(123_456L), enabled = false)

        val stored = repo.upsert(oneShot)

        assertEquals(stored, repo.get("t"))
    }
}
