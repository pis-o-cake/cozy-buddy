package app.cozybuddy.hub.core.audio

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Process
import androidx.core.content.ContextCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import timber.log.Timber

/** 16 kHz mono PCM16 `AudioRecord` 하나로 프레임을 만들어 [FrameDistributor] 에 흘린다. */
@Singleton
class AudioRecordCapture @Inject constructor(
    @ApplicationContext private val context: Context,
) : AudioCapture {

    private val distributor = FrameDistributor()
    private val _running = MutableStateFlow(false)
    override val running: StateFlow<Boolean> = _running.asStateFlow()

    @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN")
    private val lifecycleLock = Object()
    private var record: AudioRecord? = null
    private var thread: Thread? = null

    // 서비스가 캡처를 원하는 상태. 읽기 오류로 [_running] 이 잠시 꺼져도 true 로 남아 복구 대상이 된다.
    @Volatile
    private var wanted = false

    override fun start(): Boolean = synchronized(lifecycleLock) {
        if (_running.value) return true
        // 복구 스레드가 재시도 중이면 두 번째 AudioRecord 를 만들지 않는다.
        if (wanted && thread?.isAlive == true) return true
        // 읽기 오류로 루프가 스스로 끝난 잔여 객체가 있으면 먼저 정리한다.
        releaseLocked()
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) {
            Timber.w("Capture not started: RECORD_AUDIO not granted")
            return false
        }
        val created = createRecord() ?: return false
        try {
            created.startRecording()
        } catch (e: IllegalStateException) {
            Timber.e(e, "AudioRecord.startRecording failed")
            created.release()
            return false
        }
        if (created.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            Timber.w("AudioRecord did not enter recording state (mic in use?)")
            created.release()
            return false
        }
        record = created
        _running.value = true
        wanted = true
        thread = Thread({ captureAndRecover(created) }, "audio-capture").also { it.start() }
        Timber.i("Audio capture started")
        true
    }

    override fun stop() {
        val worker: Thread?
        synchronized(lifecycleLock) {
            wanted = false
            _running.value = false
            worker = thread
            record?.let { runCatching { it.stop() } }
            lifecycleLock.notifyAll()
        }
        // 읽기 루프가 끝난 뒤에 release 해야 네이티브 객체 해제와 read 가 겹치지 않는다.
        worker?.join(JOIN_TIMEOUT_MS)
        synchronized(lifecycleLock) { releaseLocked() }
        distributor.reset()
        Timber.i("Audio capture stopped")
    }

    override fun subscribe(preRollMs: Int): FrameSubscription = distributor.subscribe(preRollMs)

    private fun releaseRecordLocked() {
        record?.release()
        record = null
    }

    private fun releaseLocked() {
        releaseRecordLocked()
        thread = null
    }

    @SuppressLint("MissingPermission")
    private fun createRecord(): AudioRecord? {
        val minBytes = AudioRecord.getMinBufferSize(
            CaptureFormat.SAMPLE_RATE_HZ,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBytes <= 0) {
            Timber.e("Unsupported capture parameters: minBufferSize=%d", minBytes)
            return null
        }
        val frameBytes = CaptureFormat.FRAME_SAMPLES * CaptureFormat.BYTES_PER_SAMPLE
        val bufferBytes = maxOf(minBytes, frameBytes * BUFFER_FRAMES)
        return try {
            val created = AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(CaptureFormat.SAMPLE_RATE_HZ)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build(),
                )
                .setBufferSizeInBytes(bufferBytes)
                .build()
            if (created.state != AudioRecord.STATE_INITIALIZED) {
                Timber.e("AudioRecord failed to initialize")
                created.release()
                null
            } else {
                created
            }
        } catch (e: UnsupportedOperationException) {
            Timber.e(e, "AudioRecord build failed")
            null
        } catch (e: SecurityException) {
            Timber.e(e, "AudioRecord build denied")
            null
        }
    }

    /** 읽기 오류로 루프가 끝나면 [CaptureRetryPolicy] 간격으로 AudioRecord 를 다시 열어 캡처를 이어간다. */
    private fun captureAndRecover(initial: AudioRecord) {
        var current: AudioRecord = initial
        while (wanted) {
            captureLoop(current)
            if (!wanted) return
            current = recover() ?: return
        }
    }

    private fun recover(): AudioRecord? {
        val policy = CaptureRetryPolicy()
        synchronized(lifecycleLock) {
            _running.value = false
            releaseRecordLocked()
        }
        while (true) {
            val delayMs = policy.nextDelayMs()
            Timber.w("Audio capture recovery in %d ms", delayMs)
            synchronized(lifecycleLock) {
                if (!wanted) return null
                try {
                    lifecycleLock.wait(delayMs)
                } catch (e: InterruptedException) {
                    Thread.currentThread().interrupt()
                    return null
                }
                if (!wanted) return null
                val reopened = openRecordLocked()
                if (reopened != null) {
                    Timber.i("Audio capture recovered")
                    return reopened
                }
            }
        }
    }

    private fun openRecordLocked(): AudioRecord? {
        val created = createRecord() ?: return null
        try {
            created.startRecording()
        } catch (e: IllegalStateException) {
            Timber.e(e, "AudioRecord.startRecording failed during recovery")
            created.release()
            return null
        }
        if (created.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            created.release()
            return null
        }
        record = created
        _running.value = true
        return created
    }

    private fun captureLoop(source: AudioRecord) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_AUDIO)
        var frame = ShortArray(CaptureFormat.FRAME_SAMPLES)
        var filled = 0
        while (_running.value) {
            val read = source.read(frame, filled, frame.size - filled, AudioRecord.READ_BLOCKING)
            if (read < 0) {
                Timber.e("AudioRecord.read failed: %d", read)
                return
            }
            filled += read
            if (filled < frame.size) continue
            distributor.publish(frame)
            // 구독자가 같은 배열을 계속 참조하므로 재사용하지 않는다.
            frame = ShortArray(CaptureFormat.FRAME_SAMPLES)
            filled = 0
        }
    }

    private companion object {
        const val BUFFER_FRAMES = 8
        const val JOIN_TIMEOUT_MS = 1_000L
    }
}
