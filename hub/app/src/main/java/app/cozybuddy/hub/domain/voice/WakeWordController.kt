package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.AudioCapture
import app.cozybuddy.hub.core.audio.wakeword.WakeSensitivity
import app.cozybuddy.hub.core.audio.wakeword.WakeSpeakingPolicy
import app.cozybuddy.hub.core.audio.wakeword.WakeWordConfig
import app.cozybuddy.hub.core.audio.wakeword.WakeWordEngine
import app.cozybuddy.hub.core.audio.wakeword.WakeWordSettings
import app.cozybuddy.hub.core.audio.wakeword.WakeWordStatus
import app.cozybuddy.hub.di.ApplicationScope
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import timber.log.Timber

/** 프레임을 엔진에 넣는 방식. 어시스턴트 상태에서 정해진다. */
internal enum class WakeMode { NORMAL, RAISED, OFF }

/**
 * 캡처 프레임을 호출어 엔진에 계속 공급하고, 감지되면 [VoiceClient.onWakeWord] 를 부른다.
 *
 * - IDLE: 설정 문턱으로 감지한다. 알람이 울리는 중에도 켜 둔다("자비스, 알람 꺼").
 * - SPEAKING: 기본은 문턱을 올린다. 우리 TTS 소리에 반응하지 않고 큰 소리의 끼어들기만 허용한다. 정책이 GATE_OFF 면 끈다.
 * - LISTENING·THINKING: 이미 대화 중이므로 프레임을 넣지 않는다. 다시 IDLE 이 되면 엔진 상태를 비운다.
 *
 * IMPORTANT: 프레임은 기기 안에서 엔진으로만 간다. 감지 전에는 어떤 오디오도 [VoiceClient] 나 네트워크로 가지 않는다.
 */
@Singleton
class WakeWordController @Inject constructor(
    private val engine: WakeWordEngine,
    private val capture: AudioCapture,
    private val settings: WakeWordSettings,
    private val machine: AssistantStateMachine,
    private val voiceClient: VoiceClient,
    private val config: WakeWordConfig,
    @ApplicationScope private val scope: CoroutineScope,
) {
    private var job: Job? = null

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch {
            launch { engine.events.collect { voiceClient.onWakeWord() } }
            // 설정이 켜져 있고 마이크가 돌 때만 엔진을 올린다. 둘 중 하나라도 꺼지면 collectLatest 가 루프를 취소한다.
            combine(settings.prefs, capture.running) { prefs, running -> prefs to (running && prefs.enabled) }
                .distinctUntilChanged()
                .collectLatest { (prefs, active) ->
                    if (!active) return@collectLatest
                    listen(prefs.sensitivity)
                }
        }
        Timber.i("Wake word controller started")
    }

    fun stop() {
        job?.cancel()
        job = null
        engine.release()
        Timber.i("Wake word controller stopped")
    }

    private suspend fun listen(sensitivity: Float) {
        if (!engine.initialize()) return Timber.w("Wake word disabled: engine unavailable")
        val baseThreshold = WakeSensitivity.toThreshold(sensitivity)
        engine.setThreshold(baseThreshold)
        engine.reset()
        val subscription = capture.subscribe(0)
        var mode = WakeMode.NORMAL
        try {
            for (frame in subscription.live) {
                val next = modeFor(machine.state.value.phase)
                if (next != mode) {
                    // 대화 중에 쌓이지 않은 오디오와 이어 붙이면 점수가 오염되므로 NORMAL 로 돌아올 때 비운다.
                    if (next == WakeMode.NORMAL || mode == WakeMode.OFF) engine.reset()
                    engine.setThreshold(thresholdFor(next, baseThreshold))
                    mode = next
                }
                if (mode == WakeMode.OFF) continue
                engine.process(frame)
                if (engine.status.value is WakeWordStatus.Failed) return Timber.w("Wake word stopped: engine failed")
            }
        } catch (e: CancellationException) {
            throw e
        } finally {
            subscription.close()
        }
    }

    internal fun modeFor(phase: AssistantPhase): WakeMode = when (phase) {
        AssistantPhase.IDLE -> WakeMode.NORMAL
        AssistantPhase.SPEAKING ->
            if (config.speakingPolicy == WakeSpeakingPolicy.GATE_OFF) WakeMode.OFF else WakeMode.RAISED
        AssistantPhase.LISTENING, AssistantPhase.THINKING -> WakeMode.OFF
    }

    private fun thresholdFor(mode: WakeMode, base: Float): Float = when (mode) {
        WakeMode.RAISED -> (base + config.speakingThresholdBoost).coerceAtMost(WakeSensitivity.THRESHOLD_CEILING)
        else -> base
    }
}
