package app.cozybuddy.hub.data.scheduling

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import app.cozybuddy.hub.MainActivity
import app.cozybuddy.hub.domain.scheduling.AlarmScheduler
import app.cozybuddy.hub.feature.alarm.service.AlarmReceiver
import dagger.hilt.android.qualifiers.ApplicationContext
import java.time.Instant
import javax.inject.Inject
import javax.inject.Singleton
import timber.log.Timber

/**
 * [AlarmManager.setAlarmClock] 기반 예약.
 *
 * setAlarmClock 은 Doze 를 뚫고, 백그라운드 FGS 시작 제한에서 면제된다.
 */
@Singleton
class AndroidAlarmScheduler @Inject constructor(
    @ApplicationContext private val context: Context,
) : AlarmScheduler {

    private val alarmManager: AlarmManager = checkNotNull(context.getSystemService(AlarmManager::class.java))

    override fun canScheduleExact(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarmManager.canScheduleExactAlarms()

    override fun schedule(alarmId: String, triggerAt: Instant) = set(alarmId, SLOT_REGULAR, triggerAt)

    override fun scheduleSnooze(alarmId: String, triggerAt: Instant) = set(alarmId, SLOT_SNOOZE, triggerAt)

    override fun cancel(alarmId: String) {
        listOf(SLOT_REGULAR, SLOT_SNOOZE).forEach { slot ->
            val pending = firePendingIntent(alarmId, slot, PendingIntent.FLAG_NO_CREATE) ?: return@forEach
            alarmManager.cancel(pending)
            pending.cancel()
        }
        Timber.d("Alarm schedule cancelled id=%s", alarmId)
    }

    private fun set(alarmId: String, slot: String, triggerAt: Instant) {
        if (!canScheduleExact()) {
            // 권한이 없으면 SecurityException 이므로 예약하지 않고 UI 가 안내하게 둔다.
            Timber.w("Exact alarm permission missing; skip schedule id=%s", alarmId)
            return
        }
        val operation = checkNotNull(firePendingIntent(alarmId, slot, 0))
        val info = AlarmManager.AlarmClockInfo(triggerAt.toEpochMilli(), showPendingIntent())
        try {
            alarmManager.setAlarmClock(info, operation)
            Timber.i("Alarm scheduled id=%s slot=%s at=%s", alarmId, slot, triggerAt)
        } catch (e: SecurityException) {
            Timber.w(e, "Exact alarm denied id=%s", alarmId)
        }
    }

    private fun firePendingIntent(alarmId: String, slot: String, extraFlags: Int): PendingIntent? {
        // data URI 로 알람·슬롯마다 서로 다른 PendingIntent 가 되게 한다(requestCode 충돌 회피).
        val uri = Uri.Builder().scheme(URI_SCHEME).authority(slot).appendPath(alarmId).build()
        val intent = Intent(context, AlarmReceiver::class.java)
            .setAction(AlarmReceiver.ACTION_FIRE)
            .setData(uri)
            .putExtra(AlarmReceiver.EXTRA_ALARM_ID, alarmId)
        return PendingIntent.getBroadcast(context, 0, intent, PendingIntent.FLAG_IMMUTABLE or extraFlags)
    }

    private fun showPendingIntent(): PendingIntent = PendingIntent.getActivity(
        context,
        0,
        Intent(context, MainActivity::class.java),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private companion object {
        const val URI_SCHEME = "cozy-alarm"
        const val SLOT_REGULAR = "regular"
        const val SLOT_SNOOZE = "snooze"
    }
}
