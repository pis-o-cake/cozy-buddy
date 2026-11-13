package app.cozybuddy.hub.domain.scheduling

import java.time.Instant
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class SnoozePolicyTest {

    @Test
    fun `snooze fires nine minutes later`() {
        val now = Instant.parse("2026-11-13T00:00:30Z")

        assertEquals(Instant.parse("2026-11-13T00:09:30Z"), SnoozePolicy.snoozeAt(now))
    }
}
