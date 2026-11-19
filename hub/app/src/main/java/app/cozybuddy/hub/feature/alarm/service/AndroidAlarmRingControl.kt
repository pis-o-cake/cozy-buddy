package app.cozybuddy.hub.feature.alarm.service

import android.content.Context
import android.content.Intent
import app.cozybuddy.hub.feature.alarm.AlarmRingControl
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import timber.log.Timber

class AndroidAlarmRingControl @Inject constructor(
    @ApplicationContext private val context: Context,
) : AlarmRingControl {

    override fun stop() = send(AlarmRingService.ACTION_STOP)

    override fun snooze(minutes: Int) = send(AlarmRingService.ACTION_SNOOZE) {
        putExtra(AlarmRingService.EXTRA_SNOOZE_MINUTES, minutes)
    }

    // 울리는 동안 서비스는 이미 포그라운드이므로 일반 startService 로 충분하다.
    private fun send(action: String, configure: Intent.() -> Unit = {}) {
        try {
            context.startService(Intent(context, AlarmRingService::class.java).setAction(action).apply(configure))
        } catch (e: RuntimeException) {
            Timber.e(e, "Failed to send ring control action=%s", action)
        }
    }
}
