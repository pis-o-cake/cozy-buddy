package app.cozybuddy.hub.domain.scheduling

import java.time.Duration
import java.time.Instant

object SnoozePolicy {
    const val DEFAULT_MINUTES = 9
    const val MAX_MINUTES = 60
    val DURATION: Duration = Duration.ofMinutes(DEFAULT_MINUTES.toLong())

    fun snoozeAt(now: Instant, minutes: Int = DEFAULT_MINUTES): Instant = now.plus(Duration.ofMinutes(minutes.toLong()))
}
