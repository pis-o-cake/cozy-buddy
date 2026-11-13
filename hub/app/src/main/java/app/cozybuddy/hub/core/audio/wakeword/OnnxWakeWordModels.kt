package app.cozybuddy.hub.core.audio.wakeword

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.CLASSIFIER_FRAMES
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.EMBEDDING_DIM
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.EMBEDDING_WINDOW_FRAMES
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.MEL_BINS
import java.nio.FloatBuffer
import javax.inject.Inject

/** 모델 바이트에서 [WakeWordModels] 를 만든다. 테스트가 가짜로 바꿀 수 있게 분리했다. */
fun interface WakeWordModelFactory {
    fun create(melspectrogram: ByteArray, embedding: ByteArray, keyword: ByteArray): WakeWordModels
}

class OnnxWakeWordModelFactory @Inject constructor() : WakeWordModelFactory {
    override fun create(melspectrogram: ByteArray, embedding: ByteArray, keyword: ByteArray): WakeWordModels {
        val env = OrtEnvironment.getEnvironment()
        val options = OrtSession.SessionOptions().apply {
            setIntraOpNumThreads(1)
            setInterOpNumThreads(1)
        }
        // IMPORTANT: 세션 하나라도 만들지 못하면 먼저 만든 것을 닫아 네이티브 메모리가 새지 않게 한다.
        val sessions = ArrayList<OrtSession>(SESSION_COUNT)
        try {
            sessions += env.createSession(melspectrogram, options)
            sessions += env.createSession(embedding, options)
            sessions += env.createSession(keyword, options)
        } catch (e: Exception) {
            sessions.forEach { it.close() }
            options.close()
            throw e
        }
        return OnnxWakeWordModels(env, options, sessions[0], sessions[1], sessions[2])
    }

    private companion object {
        const val SESSION_COUNT = 3
    }
}

/**
 * ONNX Runtime 로 돌리는 openWakeWord 세 모델. Silero VAD 와 같은 런타임을 쓴다.
 *
 * 입출력 이름은 공개 모델 기준이다: 멜 `input`, 임베딩 `input_1`, 분류기는 첫 번째 입력.
 * LiveKit wakeword 가 내는 분류기도 같은 `[1, 16, 96] -> [1, 1]` 형태라 파일만 바꿔 끼울 수 있다.
 */
class OnnxWakeWordModels(
    private val env: OrtEnvironment,
    private val options: OrtSession.SessionOptions,
    private val mel: OrtSession,
    private val embedding: OrtSession,
    private val keyword: OrtSession,
) : WakeWordModels {

    private val keywordInput: String = keyword.inputNames.first()

    override fun melspectrogram(samples: FloatArray): FloatArray =
        run(mel, MEL_INPUT, samples, longArrayOf(1, samples.size.toLong()))

    override fun embedding(melWindow: FloatArray): FloatArray =
        run(
            embedding,
            EMBEDDING_INPUT,
            melWindow,
            longArrayOf(1, EMBEDDING_WINDOW_FRAMES.toLong(), MEL_BINS.toLong(), 1),
        )

    override fun classify(embeddings: FloatArray): Float {
        val out = run(
            keyword,
            keywordInput,
            embeddings,
            longArrayOf(1, CLASSIFIER_FRAMES.toLong(), EMBEDDING_DIM.toLong()),
        )
        return out[0]
    }

    override fun close() {
        mel.close()
        embedding.close()
        keyword.close()
        options.close()
    }

    private fun run(session: OrtSession, inputName: String, data: FloatArray, shape: LongArray): FloatArray {
        OnnxTensor.createTensor(env, FloatBuffer.wrap(data), shape).use { input ->
            session.run(mapOf(inputName to input)).use { result ->
                val buffer = (result[0] as OnnxTensor).floatBuffer
                return FloatArray(buffer.remaining()).also { buffer.get(it) }
            }
        }
    }

    private companion object {
        const val MEL_INPUT = "input"
        const val EMBEDDING_INPUT = "input_1"
    }
}
