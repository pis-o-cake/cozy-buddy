package app.cozybuddy.hub.feature.alarm.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import app.cozybuddy.hub.MainActivity
import app.cozybuddy.hub.R
import app.cozybuddy.hub.domain.scheduling.SnoozePolicy
import timber.log.Timber

internal object AlarmNotifications {
    const val CHANNEL_ID = "alarm_ring"
    const val NOTIFICATION_ID = 1001

    fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            context.getString(R.string.alarm_notification_channel_name),
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            // 소리는 서비스가 USAGE_ALARM 으로 직접 재생하므로 채널은 조용해야 이중 재생이 없다.
            setSound(null, null)
            enableVibration(false)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        manager.createNotificationChannel(channel)
    }

    fun build(context: Context, title: String): Notification {
        val openApp = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_alarm)
            .setContentTitle(title)
            .setContentText(context.getString(R.string.alarm_notification_text))
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setContentIntent(openApp)
            .addAction(0, context.getString(R.string.alarm_ring_stop), servicePending(context, AlarmRingService.ACTION_STOP))
            .addAction(
                0,
                context.getString(R.string.alarm_ring_snooze, SnoozePolicy.DURATION.toMinutes().toInt()),
                servicePending(context, AlarmRingService.ACTION_SNOOZE),
            )
        if (canUseFullScreenIntent(context)) {
            builder.setFullScreenIntent(openApp, true)
        } else {
            // 권한이 회수된 경우 헤드업 알림과 앱이 이미 보이는 상태의 오버레이에 의존한다.
            Timber.w("Full-screen intent not permitted; falling back to heads-up notification")
        }
        return builder.build()
    }

    private fun canUseFullScreenIntent(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return true
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        return manager.canUseFullScreenIntent()
    }

    private fun servicePending(context: Context, action: String): PendingIntent = PendingIntent.getService(
        context,
        0,
        Intent(context, AlarmRingService::class.java).setAction(action),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )
}
