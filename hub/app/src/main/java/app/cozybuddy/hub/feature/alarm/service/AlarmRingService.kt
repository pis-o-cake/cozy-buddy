package app.cozybuddy.hub.feature.alarm.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import app.cozybuddy.hub.R
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmKind
import app.cozybuddy.hub.domain.usecase.AlarmCoordinator
import app.cozybuddy.hub.feature.alarm.AlarmRingConfig
import app.cozybuddy.hub.feature.alarm.RingingState
import app.cozybuddy.hub.feature.alarm.RingingStateHolder
import androidx.core.app.ServiceCompat
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import timber.log.Timber

/**
 * 알람을 울리는 mediaPlayback 포그라운드 서비스.
 *
 * `USAGE_ALARM` 으로 반복 재생하며 볼륨을 서서히 올리고, [AlarmRingConfig.AUTO_STOP] 뒤 스스로 멈춘다.
 *
 * 울리는 중에 다른 알람이 겹치면 대기열에 두고, 현재 알람이 정지·스누즈된 뒤 순서대로 울린다.
 */
@AndroidEntryPoint
class AlarmRingService : Service() {

    @Inject lateinit var coordinator: AlarmCoordinator
    @Inject lateinit var ringingStateHolder: RingingStateHolder
    @Inject lateinit var timeSource: TimeSource

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var session: Session? = null
    private var starting = false
    private val pendingIds = ArrayDeque<String>()
    private var player: MediaPlayer? = null
    private var focusRequest: AudioFocusRequest? = null

    private class Session(val alarmId: String, val jobs: MutableList<Job> = mutableListOf())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> handleStart(intent.getStringExtra(EXTRA_ALARM_ID))
            ACTION_STOP -> finishRing(snoozed = false)
            ACTION_SNOOZE -> finishRing(snoozed = true)
            else -> stopSelf()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        releaseAudio()
        ringingStateHolder.clear()
        scope.cancel()
        super.onDestroy()
    }

    private fun handleStart(alarmId: String?) {
        // IMPORTANT: startForegroundService 후 5초 안에 startForeground 를 부르지 않으면 앱이 죽는다. DB 조회보다 먼저 호출한다.
        enterForeground(getString(R.string.alarm_default_label))
        if (alarmId == null) {
            Timber.w("Ring start without alarm id")
            stopSelf()
            return
        }
        // 메인 스레드에서 동기로 판단해야 onFired 대기 중 들어온 두 번째 시작도 대기열로 간다.
        if (session != null || starting) {
            enqueue(alarmId)
            return
        }
        startRing(alarmId)
    }

    private fun enqueue(alarmId: String) {
        if (alarmId == session?.alarmId || alarmId in pendingIds) {
            Timber.d("Duplicate ring request ignored id=%s", alarmId)
            return
        }
        pendingIds.addLast(alarmId)
        Timber.i("Ring queued behind active alarm id=%s queued=%d", alarmId, pendingIds.size)
    }

    private fun startRing(alarmId: String) {
        starting = true
        scope.launch {
            val alarm = try {
                coordinator.onFired(alarmId)
            } finally {
                starting = false
            }
            if (alarm == null) {
                Timber.w("Alarm vanished before ring id=%s", alarmId)
                startNextOrStop()
                return@launch
            }
            beginRing(alarm)
        }
    }

    private fun startNextOrStop() {
        val next = pendingIds.removeFirstOrNull()
        if (next == null) {
            stopSelf()
            return
        }
        Timber.i("Starting queued ring id=%s", next)
        startRing(next)
    }

    private fun beginRing(alarm: Alarm) {
        val newSession = Session(alarm.id)
        session = newSession
        val title = alarm.label.ifBlank { defaultLabel(alarm.kind) }
        enterForeground(title)
        ringingStateHolder.start(
            RingingState.Ringing(
                alarmId = alarm.id,
                kind = alarm.kind,
                label = alarm.label,
                startedAtEpochMs = timeSource.now().toInstant().toEpochMilli(),
            ),
        )
        startPlayback()
        newSession.jobs += scope.launch { rampVolume() }
        newSession.jobs += scope.launch {
            delay(AlarmRingConfig.AUTO_STOP.toMillis())
            Timber.i("Auto stop after timeout id=%s", alarm.id)
            finishRing(snoozed = false)
        }
        Timber.i("Ringing started id=%s", alarm.id)
    }

    private fun finishRing(snoozed: Boolean) {
        if (session == null) {
            // 다음 알람을 준비하는 중이면 서비스를 끄지 않는다.
            if (!starting) stopSelf()
            return
        }
        scope.launch { finishCurrent(snoozed) }
    }

    private suspend fun finishCurrent(snoozed: Boolean) {
        val finished = session ?: return
        session = null
        // 정리하는 동안 들어온 시작 요청이 대기열 순서를 앞지르지 않게 한다.
        starting = true
        finished.jobs.forEach { it.cancel() }
        releaseAudio()
        ringingStateHolder.clear()
        // 서비스 종료 중에도 DB 갱신(일회성 비활성화·스누즈 예약)은 끝까지 해야 한다.
        withContext(NonCancellable) {
            try {
                coordinator.onRingFinished(finished.alarmId, snoozed)
            } catch (e: Exception) {
                Timber.e(e, "onRingFinished failed id=%s", finished.alarmId)
            }
        }
        Timber.i("Ringing finished id=%s snoozed=%b", finished.alarmId, snoozed)
        starting = false
        if (pendingIds.isNotEmpty()) {
            startNextOrStop()
            return
        }
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun enterForeground(title: String) {
        AlarmNotifications.ensureChannel(this)
        ServiceCompat.startForeground(
            this,
            AlarmNotifications.NOTIFICATION_ID,
            AlarmNotifications.build(this, title),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK,
        )
    }

    private fun defaultLabel(kind: AlarmKind): String = getString(
        if (kind == AlarmKind.TIMER) R.string.alarm_default_label_timer else R.string.alarm_default_label,
    )

    private fun startPlayback() {
        val attributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()
        requestFocus(attributes)
        for ((index, source) in ringtoneSources().withIndex()) {
            val candidate = MediaPlayer()
            try {
                candidate.setAudioAttributes(attributes)
                source(candidate)
                candidate.isLooping = true
                candidate.setVolume(AlarmRingConfig.START_VOLUME, AlarmRingConfig.START_VOLUME)
                candidate.setWakeMode(this, PowerManager.PARTIAL_WAKE_LOCK)
                candidate.prepare()
                candidate.start()
                player = candidate
                Timber.i("Alarm audio started source=%d", index)
                return
            } catch (e: Exception) {
                Timber.w(e, "Alarm audio source %d failed", index)
                candidate.release()
            }
        }
        Timber.e("All alarm audio sources failed")
    }

    // 사용자 기본 알람음 -> 시스템 기본 URI -> 앱에 내장한 톤 순으로 시도한다.
    private fun ringtoneSources(): List<(MediaPlayer) -> Unit> = listOf(
        { p ->
            val uri = RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_ALARM)
            p.setDataSource(this, checkNotNull(uri) { "no default alarm ringtone" })
        },
        { p -> p.setDataSource(this, Settings.System.DEFAULT_ALARM_ALERT_URI) },
        { p -> resources.openRawResourceFd(R.raw.alarm_fallback).use { p.setDataSource(it) } },
    )

    private suspend fun rampVolume() {
        val startedAt = System.nanoTime()
        while (true) {
            val elapsedMs = (System.nanoTime() - startedAt) / NANOS_PER_MILLI
            val volume = AlarmRingConfig.volumeAt(elapsedMs)
            player?.setVolume(volume, volume)
            if (volume >= 1f) return
            delay(AlarmRingConfig.RAMP_STEP_MS)
        }
    }

    private fun requestFocus(attributes: AudioAttributes) {
        val manager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(attributes)
            .setOnAudioFocusChangeListener { }
            .build()
        focusRequest = request
        manager.requestAudioFocus(request)
    }

    private fun releaseAudio() {
        player?.let {
            runCatching { it.stop() }
            it.release()
        }
        player = null
        focusRequest?.let {
            (getSystemService(Context.AUDIO_SERVICE) as AudioManager).abandonAudioFocusRequest(it)
        }
        focusRequest = null
    }

    companion object {
        const val ACTION_START = "app.cozybuddy.hub.action.RING_START"
        const val ACTION_STOP = "app.cozybuddy.hub.action.RING_STOP"
        const val ACTION_SNOOZE = "app.cozybuddy.hub.action.RING_SNOOZE"
        const val EXTRA_ALARM_ID = "alarm_id"
        private const val NANOS_PER_MILLI = 1_000_000L

        fun startIntent(context: Context, alarmId: String): Intent =
            Intent(context, AlarmRingService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_ALARM_ID, alarmId)
    }
}
