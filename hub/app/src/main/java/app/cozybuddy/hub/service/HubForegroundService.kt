package app.cozybuddy.hub.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import app.cozybuddy.hub.MainActivity
import app.cozybuddy.hub.R
import app.cozybuddy.hub.core.audio.AudioCapture
import app.cozybuddy.hub.domain.voice.VoiceClient
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import timber.log.Timber

/**
 * 마이크 캡처와 응답 재생을 붙들어 두는 포그라운드 서비스(`microphone|mediaPlayback`).
 *
 * IMPORTANT: 반드시 보이는 [MainActivity] 에서 시작한다. BOOT_COMPLETED 같은 백그라운드 경로에서 시작하면
 * Android 14 이상에서 마이크 FGS 가 거부되고, Android 17 은 오디오도 무시한다.
 * 그래서 시스템이 서비스를 죽였을 때 스스로 되살아나지 않도록 [START_NOT_STICKY] 를 쓰고, 복구는 HOME 앱 Activity 에 맡긴다.
 *
 * RECORD_AUDIO 가 없으면 mediaPlayback 만으로 떠서 응답 재생은 가능한 저하 상태가 된다.
 */
@AndroidEntryPoint
class HubForegroundService : Service() {

    @Inject lateinit var capture: AudioCapture
    @Inject lateinit var voiceClient: VoiceClient

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val micGranted = hasMicPermission()
        if (!enterForeground(micGranted)) {
            stopSelf()
            return START_NOT_STICKY
        }
        val capturing = micGranted && capture.start()
        if (micGranted && !capturing) Timber.w("Microphone permitted but capture could not start")
        voiceClient.start()
        if (!capturing) updateNotification(micAvailable = false)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        voiceClient.stop()
        capture.stop()
        super.onDestroy()
    }

    private fun enterForeground(micGranted: Boolean): Boolean {
        val type = foregroundType(micGranted)
        return try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(micGranted), type)
            true
        } catch (e: SecurityException) {
            Timber.e(e, "Foreground service start denied")
            false
        } catch (e: IllegalStateException) {
            // Android 12 이상에서 앱이 보이지 않을 때 시작하면 ForegroundServiceStartNotAllowedException 이 난다.
            Timber.e(e, "Foreground service start not allowed")
            false
        }
    }

    private fun foregroundType(micGranted: Boolean): Int {
        var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
        if (micGranted && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        }
        return type
    }

    private fun hasMicPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun updateNotification(micAvailable: Boolean) {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.notify(NOTIFICATION_ID, buildNotification(micAvailable))
    }

    private fun buildNotification(micAvailable: Boolean): Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val text = if (micAvailable) R.string.voice_service_ready else R.string.voice_service_mic_missing
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_mic)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(text))
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(openApp)
            .build()
    }

    private fun ensureChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.voice_service_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        )
        manager.createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "voice_service"
        private const val NOTIFICATION_ID = 1002

        /** 보이는 Activity 에서만 호출한다. 이미 떠 있으면 권한 변화를 반영해 다시 올라온다. */
        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, HubForegroundService::class.java))
        }
    }
}
