package app.cozybuddy.hub.domain.scheduling

import java.time.Duration
import java.time.Instant

object SnoozePolicy {
    val DURATION: Duration = Duration.ofMinutes(9)

    fun snoozeAt(now: Instant): Instant = now.plus(DURATION)
}
