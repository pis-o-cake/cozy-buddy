package app.cozybuddy.hub.feature.alarm.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import timber.log.Timber

/**
 * 알람 정각 수신. 오디오는 여기서 재생하지 않고 FGS 로 넘긴다.
 *
 * setAlarmClock 으로 시작한 FGS 는 백그라운드 시작 제한에서 면제되고, USAGE_ALARM 재생은 Android 17 의
 * 백그라운드 오디오 제한 예외(exact alarm 권한)에 해당한다.
 */
class AlarmReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_FIRE) return
        val alarmId = intent.getStringExtra(EXTRA_ALARM_ID)
        if (alarmId == null) {
            Timber.w("Alarm fire without id")
            return
        }
        try {
            context.startForegroundService(AlarmRingService.startIntent(context, alarmId))
        } catch (e: RuntimeException) {
            Timber.e(e, "Failed to start ring service id=%s", alarmId)
        }
    }

    companion object {
        const val ACTION_FIRE = "app.cozybuddy.hub.action.ALARM_FIRE"
        const val EXTRA_ALARM_ID = "alarm_id"
    }
}
