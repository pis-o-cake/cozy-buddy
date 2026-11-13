package app.cozybuddy.hub.core.audio

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import java.nio.FloatBuffer
import java.nio.LongBuffer
import java.util.concurrent.Executors
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import timber.log.Timber

/**
 * Silero VAD v5(ONNX) 구현. 16 kHz 512 샘플 프레임을 받아 음성 확률을 낸다.
 *
 * IMPORTANT: 세션과 RNN 상태는 전용 단일 스레드에서만 만진다. 호출자 스레드와 무관하게 추론이 직렬화된다.
 *
 * 모델 입력은 `input[1, 64 + 512]`(직전 프레임 끝 64샘플 컨텍스트 + 현재 프레임), `state[2, 1, 128]`, `sr`(스칼라 int64)다.
 * 컨텍스트를 붙이지 않으면 확률이 눈에 띄게 낮아진다.
 */
@Singleton
class SileroVad @Inject constructor(
    @ApplicationContext private val context: Context,
) : Vad {

    private val dispatcher = Executors.newSingleThreadExecutor { Thread(it, "vad-inference") }.asCoroutineDispatcher()

    // 아래 필드는 모두 vad-inference 스레드 전용이다.
    private var env: OrtEnvironment? = null
    private var session: OrtSession? = null
    private var state = FloatArray(STATE_SIZE)
    private var contextTail = FloatArray(CONTEXT_SAMPLES)
    private var resetRequested = false

    override suspend fun speechProbability(frame: ShortArray): Float = withContext(dispatcher) {
        require(frame.size == CaptureFormat.FRAME_SAMPLES) { "Expected ${CaptureFormat.FRAME_SAMPLES} samples" }
        if (resetRequested) {
            clearState()
            resetRequested = false
        }
        infer(frame)
    }

    override fun reset() {
        // 추론 중인 호출과 경쟁하지 않도록 플래그만 세우고 다음 추론 직전에 비운다.
        resetRequested = true
    }

    private fun clearState() {
        state = FloatArray(STATE_SIZE)
        contextTail = FloatArray(CONTEXT_SAMPLES)
    }

    private fun infer(frame: ShortArray): Float {
        val activeSession = session ?: openSession().also { session = it }
        val activeEnv = env ?: return 0f

        val input = FloatArray(CONTEXT_SAMPLES + frame.size)
        contextTail.copyInto(input)
        for (i in frame.indices) input[CONTEXT_SAMPLES + i] = frame[i] / PCM_SCALE

        val inputs = HashMap<String, OnnxTensor>(INPUT_COUNT)
        try {
            inputs["input"] = OnnxTensor.createTensor(activeEnv, FloatBuffer.wrap(input), longArrayOf(1, input.size.toLong()))
            inputs["state"] = OnnxTensor.createTensor(activeEnv, FloatBuffer.wrap(state), longArrayOf(2, 1, STATE_WIDTH))
            // sr 은 shape [] 인 스칼라여야 한다. 길이 1 배열로 만들면 rank 불일치로 거부된다.
            inputs["sr"] = OnnxTensor.createTensor(
                activeEnv,
                LongBuffer.wrap(longArrayOf(CaptureFormat.SAMPLE_RATE_HZ.toLong())),
                longArrayOf(),
            )
            activeSession.run(inputs).use { result ->
                val probability = (result.get("output").get().value as Array<*>).let { rows ->
                    (rows[0] as FloatArray)[0]
                }
                val nextState = (result.get("stateN").get() as OnnxTensor).floatBuffer
                nextState.get(state)
                frame.takeLast(CONTEXT_SAMPLES).forEachIndexed { i, s -> contextTail[i] = s / PCM_SCALE }
                return probability
            }
        } finally {
            inputs.values.forEach { it.close() }
        }
    }

    private fun openSession(): OrtSession {
        val ortEnv = OrtEnvironment.getEnvironment()
        env = ortEnv
        val model = context.assets.open(MODEL_ASSET).use { it.readBytes() }
        val options = OrtSession.SessionOptions().apply {
            setIntraOpNumThreads(1)
            setInterOpNumThreads(1)
        }
        Timber.i("Silero VAD model loaded: %d bytes", model.size)
        return ortEnv.createSession(model, options)
    }

    private companion object {
        const val MODEL_ASSET = "silero_vad.onnx"
        const val CONTEXT_SAMPLES = 64
        const val STATE_WIDTH = 128L
        const val STATE_SIZE = 2 * 1 * 128
        const val INPUT_COUNT = 3
        const val PCM_SCALE = 32768f
    }
}
