package app.cozybuddy.hub.core.audio.wakeword

import app.cozybuddy.hub.core.audio.CaptureFormat
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** 호출 순서와 입력을 기록하는 가짜 모델. 점수는 [scores] 를 순서대로 돌려준다. */
class FakeWakeWordModels(private val scores: List<Float> = emptyList()) : WakeWordModels {
    val melInputs = mutableListOf<FloatArray>()
    val embeddingInputs = mutableListOf<FloatArray>()
    val classifyInputs = mutableListOf<FloatArray>()
    var melFramesPerCall = 8
    private var classifyCalls = 0

    override fun melspectrogram(samples: FloatArray): FloatArray {
        melInputs += samples
        return FloatArray(melFramesPerCall * OpenWakeWordFormat.MEL_BINS) { melInputs.size.toFloat() }
    }

    override fun embedding(melWindow: FloatArray): FloatArray {
        embeddingInputs += melWindow.copyOf()
        return FloatArray(OpenWakeWordFormat.EMBEDDING_DIM) { embeddingInputs.size.toFloat() }
    }

    override fun classify(embeddings: FloatArray): Float {
        classifyInputs += embeddings.copyOf()
        return scores.getOrElse(classifyCalls++) { 0f }
    }

    override fun close() = Unit
}

class WakeWordPipelineTest {

    private fun detector(threshold: Float = 0.5f, hits: Int = 2, refractoryMs: Int = 2_000) =
        WakeWordDetector(hits, refractoryMs, threshold)

    private fun ramp(from: Int, count: Int) = ShortArray(count) { (from + it).toShort() }

    @Test
    fun `512 sample frames are regrouped into 1280 sample chunks without losing samples`() {
        val models = FakeWakeWordModels()
        val pipeline = WakeWordPipeline(models, detector())
        val total = CaptureFormat.FRAME_SAMPLES * 10
        var next = 0

        repeat(10) {
            pipeline.feed(ramp(next, CaptureFormat.FRAME_SAMPLES))
            next += CaptureFormat.FRAME_SAMPLES
        }

        // 5120 샘플 = 청크 4 개, 나머지 0.
        assertEquals(total / OpenWakeWordFormat.CHUNK_SAMPLES, models.melInputs.size)
        assertEquals(4, models.melInputs.size)
        val firstChunk = models.melInputs[0]
        assertEquals(OpenWakeWordFormat.CHUNK_SAMPLES, firstChunk.size)
        assertEquals(0f, firstChunk.first())
        assertEquals(1279f, firstChunk.last())
        // 둘째 청크 이후는 직전 480 샘플 + 새 1280 샘플이며 값이 끊김 없이 이어진다.
        val second = models.melInputs[1]
        assertEquals(OpenWakeWordFormat.MEL_CONTEXT_SAMPLES + OpenWakeWordFormat.CHUNK_SAMPLES, second.size)
        assertEquals(800f, second.first())
        assertEquals(2559f, second.last())
        for (i in 1 until second.size) assertEquals(second[i - 1] + 1f, second[i])
    }

    @Test
    fun `leftover samples carry over to the next feed`() {
        val models = FakeWakeWordModels()
        val pipeline = WakeWordPipeline(models, detector())

        pipeline.feed(ramp(0, 1_000))
        assertEquals(0, models.melInputs.size)
        pipeline.feed(ramp(1_000, 1_000))

        assertEquals(1, models.melInputs.size)
        assertEquals(1279f, models.melInputs[0].last())
        pipeline.feed(ramp(2_000, 560))
        assertEquals(2, models.melInputs.size)
        assertEquals(2_559f, models.melInputs[1].last())
    }

    @Test
    fun `a feed larger than one chunk yields several predictions in order`() {
        val models = FakeWakeWordModels()
        val pipeline = WakeWordPipeline(models, detector())
        var predictions = 0

        pipeline.feed(ramp(0, 3_000)) { predictions++ }

        assertEquals(2, predictions)
        assertEquals(2, models.melInputs.size)
        assertEquals(OpenWakeWordFormat.CHUNK_MS * 2L, pipeline.streamPositionMs)
    }

    @Test
    fun `mel output is rescaled and the window keeps the latest 76 frames`() {
        val models = FakeWakeWordModels()
        val pipeline = WakeWordPipeline(models, detector())

        repeat(2) { pipeline.feed(ramp(0, OpenWakeWordFormat.CHUNK_SAMPLES)) }

        val window = models.embeddingInputs[1]
        assertEquals(OpenWakeWordFormat.EMBEDDING_WINDOW_FRAMES * OpenWakeWordFormat.MEL_BINS, window.size)
        val bins = OpenWakeWordFormat.MEL_BINS
        // 시작 값 1, 첫 호출 출력 1/10+2, 둘째 호출 출력 2/10+2 가 시간 순으로 쌓인다.
        assertEquals(1f, window[0])
        assertEquals(1f / 10f + 2f, window[(76 - 16) * bins], 1e-6f)
        assertEquals(2f / 10f + 2f, window[(76 - 8) * bins], 1e-6f)
        assertEquals(2f / 10f + 2f, window.last(), 1e-6f)
    }

    @Test
    fun `scores are suppressed for the first five predictions`() {
        val models = FakeWakeWordModels(List(10) { 0.9f })
        val pipeline = WakeWordPipeline(models, detector(hits = 1, refractoryMs = 0))
        val results = mutableListOf<WakePrediction>()

        repeat(7) { pipeline.feed(ramp(0, OpenWakeWordFormat.CHUNK_SAMPLES)) { results += it } }

        assertEquals(List(5) { 0f } + listOf(0.9f, 0.9f), results.map { it.score })
        assertEquals(2, models.classifyInputs.size)
    }

    @Test
    fun `embeddings accumulate in the classifier input newest last`() {
        val models = FakeWakeWordModels()
        val pipeline = WakeWordPipeline(models, detector())

        repeat(7) { pipeline.feed(ramp(0, OpenWakeWordFormat.CHUNK_SAMPLES)) }

        val input = models.classifyInputs.last()
        val dim = OpenWakeWordFormat.EMBEDDING_DIM
        assertEquals(OpenWakeWordFormat.CLASSIFIER_FRAMES * dim, input.size)
        assertEquals(7f, input[input.size - 1])
        assertEquals(6f, input[input.size - dim - 1])
        assertEquals(0f, input[0])
    }

    @Test
    fun `detection fires after consecutive hits and not again within the refractory period`() {
        val models = FakeWakeWordModels(List(40) { 0.9f })
        val pipeline = WakeWordPipeline(models, detector(hits = 2, refractoryMs = 2_000))
        val detections = mutableListOf<Int>()
        var index = 0

        repeat(40) {
            index++
            pipeline.feed(ramp(0, OpenWakeWordFormat.CHUNK_SAMPLES)) { if (it.detected) detections += index }
        }

        // 예측 6, 7 에서 첫 감지. 불응 2 s = 25 예측 뒤인 33, 34 에서 다시 감지.
        assertEquals(listOf(7, 34), detections)
    }

    @Test
    fun `reset clears buffered audio and warm-up`() {
        val models = FakeWakeWordModels(List(20) { 0.9f })
        val pipeline = WakeWordPipeline(models, detector())
        pipeline.feed(ramp(0, 700))
        repeat(6) { pipeline.feed(ramp(0, OpenWakeWordFormat.CHUNK_SAMPLES)) }

        pipeline.reset()
        val results = mutableListOf<Float>()
        pipeline.feed(ramp(0, OpenWakeWordFormat.CHUNK_SAMPLES)) { results += it.score }

        assertEquals(listOf(0f), results)
        assertEquals(OpenWakeWordFormat.CHUNK_SAMPLES, models.melInputs.last().size)
        assertTrue(pipeline.streamPositionMs == OpenWakeWordFormat.CHUNK_MS.toLong())
    }
}
