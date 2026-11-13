package app.cozybuddy.hub.core.audio.wakeword

import app.cozybuddy.hub.core.audio.CaptureFormat
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import timber.log.Timber

/**
 * openWakeWord 3 모델 파이프라인 엔진. 모든 추론은 전용 단일 스레드 하나에서만 일어난다.
 *
 * IMPORTANT: 파이프라인·세션 상태는 `wakeword-inference` 스레드 전용이다. [reset] 과 [setThreshold] 는
 * 다른 스레드에서 불리므로 플래그와 volatile 값만 건드리고, 실제 반영은 다음 추론 직전에 그 스레드가 한다.
 */
@Singleton
class OpenWakeWordEngine(
    private val store: WakeWordModelStore,
    private val factory: WakeWordModelFactory,
    private val config: WakeWordConfig,
    private val executor: ExecutorService,
) : WakeWordEngine {

    @Inject
    constructor(
        store: WakeWordModelStore,
        factory: WakeWordModelFactory,
        config: WakeWordConfig,
    ) : this(store, factory, config, Executors.newSingleThreadExecutor { Thread(it, "wakeword-inference") })

    private val dispatcher: CoroutineDispatcher = executor.asCoroutineDispatcher()

    private val _status = MutableStateFlow<WakeWordStatus>(WakeWordStatus.Idle)
    override val status: StateFlow<WakeWordStatus> = _status.asStateFlow()

    private val _events = MutableSharedFlow<WakeEvent>(extraBufferCapacity = EVENT_BUFFER, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    override val events: Flow<WakeEvent> = _events.asSharedFlow()

    private val detector = WakeWordDetector(
        consecutiveHits = config.consecutiveHits,
        refractoryMs = config.refractoryMs,
        threshold = WakeSensitivity.toThreshold(WakeSensitivity.DEFAULT),
    )

    // 아래 필드는 wakeword-inference 스레드 전용이다.
    private var models: WakeWordModels? = null
    private var pipeline: WakeWordPipeline? = null

    @Volatile
    private var resetRequested = false

    override suspend fun initialize(): Boolean = withContext(dispatcher) {
        if (pipeline != null) return@withContext true
        val failure = load()
        if (failure != null) {
            _status.value = WakeWordStatus.Failed(failure)
            return@withContext false
        }
        _status.value = WakeWordStatus.Ready
        true
    }

    override suspend fun process(frame: ShortArray): Float = withContext(dispatcher) {
        require(frame.size == CaptureFormat.FRAME_SAMPLES) { "Expected ${CaptureFormat.FRAME_SAMPLES} samples" }
        val active = pipeline ?: return@withContext 0f
        if (resetRequested) {
            resetRequested = false
            active.reset()
        }
        try {
            active.feed(frame) { prediction ->
                if (prediction.detected) {
                    Timber.i("Wake word detected: score=%.3f", prediction.score)
                    _events.tryEmit(WakeEvent(prediction.score, active.streamPositionMs))
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Timber.e(e, "Wake word inference failed")
            closeModels()
            _status.value = WakeWordStatus.Failed(WakeWordFailure.RUNTIME)
            return@withContext 0f
        }
        active.lastScore
    }

    override fun setThreshold(threshold: Float) {
        detector.threshold = threshold.coerceIn(0f, WakeSensitivity.THRESHOLD_CEILING)
    }

    override fun reset() {
        resetRequested = true
    }

    override fun release() {
        // 진행 중인 추론이 끝난 뒤 같은 스레드에서 닫는다.
        executor.execute {
            closeModels()
            _status.value = WakeWordStatus.Idle
        }
    }

    private fun load(): WakeWordFailure? {
        val names = listOf(WakeWordModelFiles.MELSPECTROGRAM, WakeWordModelFiles.EMBEDDING, WakeWordModelFiles.KEYWORD)
        val bytes = names.map { store.read(it) ?: return WakeWordFailure.MODEL_MISSING }
        for ((name, content) in names.zip(bytes)) {
            if (WakeWordModelFiles.matches(name, content)) continue
            Timber.e("Wake word model checksum mismatch: %s", name)
            return WakeWordFailure.MODEL_CORRUPT
        }
        return try {
            val created = factory.create(bytes[0], bytes[1], bytes[2])
            models = created
            pipeline = WakeWordPipeline(created, detector)
            Timber.i("Wake word models loaded")
            null
        } catch (e: Exception) {
            Timber.e(e, "Wake word models could not be opened")
            WakeWordFailure.RUNTIME
        }
    }

    private fun closeModels() {
        pipeline = null
        models?.close()
        models = null
    }

    private companion object {
        const val EVENT_BUFFER = 4
    }
}
