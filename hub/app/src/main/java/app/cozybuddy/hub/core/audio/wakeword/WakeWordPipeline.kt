package app.cozybuddy.hub.core.audio.wakeword

import app.cozybuddy.hub.core.audio.CaptureFormat
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.CHUNK_SAMPLES
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.CLASSIFIER_FRAMES
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.EMBEDDING_DIM
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.EMBEDDING_WINDOW_FRAMES
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.MEL_BINS
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.MEL_CONTEXT_SAMPLES
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordFormat.WARMUP_PREDICTIONS

/** 파이프라인이 청크 하나를 처리한 결과. */
data class WakePrediction(val score: Float, val detected: Boolean)

/**
 * openWakeWord 스트리밍 파이프라인: 512 샘플 캡처 프레임을 1280 샘플 청크로 다시 묶고,
 * 멜 스펙트로그램 → 임베딩(76 프레임 창) → 분류기(임베딩 16 개)로 점수를 낸다.
 *
 * IMPORTANT: 스레드 안전하지 않다. 호출자가 단일 스레드로 직렬화한다. 상태는 [reset] 으로만 비운다.
 *
 * 참조 구현(openWakeWord `AudioFeatures`)과 같은 수치를 내야 하므로 다음을 지킨다.
 * - 멜 모델 입력은 int16 값을 정규화 없이 float 로 쓴다.
 * - 청크마다 직전 480 샘플을 앞에 붙여 멜 프레임이 청크 경계에서 끊기지 않게 한다(첫 청크 제외).
 * - 멜 출력은 `x / 10 + 2` 로 변환한다.
 * - 멜 버퍼는 1 로, 임베딩 버퍼는 0 으로 시작하고, 처음 5 번의 예측은 0 점으로 눌러 둔다.
 */
class WakeWordPipeline(
    private val models: WakeWordModels,
    private val detector: WakeWordDetector,
) {
    private val pending = ShortArray(CHUNK_SAMPLES)
    private var pendingCount = 0
    private var tail = FloatArray(0)
    private val melWindow = FloatArray(EMBEDDING_WINDOW_FRAMES * MEL_BINS)
    private val embeddings = FloatArray(CLASSIFIER_FRAMES * EMBEDDING_DIM)
    private var predictions = 0
    private var processedSamples = 0L

    /** 지금까지 넣은 오디오 길이(ms). 청크 단위로 올라간다. */
    val streamPositionMs: Long get() = processedSamples * 1_000 / CaptureFormat.SAMPLE_RATE_HZ

    /** 가장 최근 예측 점수. 아직 예측이 없으면 0. */
    var lastScore: Float = 0f
        private set

    init {
        reset()
    }

    fun reset() {
        pendingCount = 0
        tail = FloatArray(0)
        melWindow.fill(1f)
        embeddings.fill(0f)
        predictions = 0
        processedSamples = 0
        lastScore = 0f
        detector.reset()
    }

    /** 샘플을 넣고, 1280 샘플이 찰 때마다 예측 하나를 만든다. 샘플은 하나도 버려지지 않는다. */
    fun feed(samples: ShortArray, onPrediction: (WakePrediction) -> Unit = {}) {
        var offset = 0
        while (offset < samples.size) {
            val take = minOf(CHUNK_SAMPLES - pendingCount, samples.size - offset)
            System.arraycopy(samples, offset, pending, pendingCount, take)
            pendingCount += take
            offset += take
            if (pendingCount < CHUNK_SAMPLES) continue
            pendingCount = 0
            onPrediction(predict())
        }
    }

    private fun predict(): WakePrediction {
        val input = FloatArray(tail.size + CHUNK_SAMPLES)
        tail.copyInto(input)
        for (i in 0 until CHUNK_SAMPLES) input[tail.size + i] = pending[i].toFloat()
        tail = input.copyOfRange(maxOf(0, input.size - MEL_CONTEXT_SAMPLES), input.size)
        processedSamples += CHUNK_SAMPLES

        appendMel(models.melspectrogram(input))
        val embedding = models.embedding(melWindow)
        require(embedding.size == EMBEDDING_DIM) { "Unexpected embedding size ${embedding.size}" }
        System.arraycopy(embeddings, EMBEDDING_DIM, embeddings, 0, embeddings.size - EMBEDDING_DIM)
        embedding.copyInto(embeddings, embeddings.size - EMBEDDING_DIM)

        predictions++
        val score = if (predictions <= WARMUP_PREDICTIONS) 0f else models.classify(embeddings)
        lastScore = score
        return WakePrediction(score, detector.onScore(score))
    }

    private fun appendMel(raw: FloatArray) {
        require(raw.size % MEL_BINS == 0) { "Mel output is not a multiple of $MEL_BINS" }
        val frames = raw.size / MEL_BINS
        val keep = EMBEDDING_WINDOW_FRAMES - minOf(frames, EMBEDDING_WINDOW_FRAMES)
        if (keep > 0) System.arraycopy(melWindow, (EMBEDDING_WINDOW_FRAMES - keep) * MEL_BINS, melWindow, 0, keep * MEL_BINS)
        val firstUsed = maxOf(0, frames - EMBEDDING_WINDOW_FRAMES)
        for (i in 0 until (frames - firstUsed) * MEL_BINS) {
            melWindow[keep * MEL_BINS + i] = raw[firstUsed * MEL_BINS + i] / MEL_SCALE + MEL_OFFSET
        }
    }

    private companion object {
        const val MEL_SCALE = 10f
        const val MEL_OFFSET = 2f
    }
}
