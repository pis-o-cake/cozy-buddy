package app.cozybuddy.hub.core.audio.wakeword

import app.cozybuddy.hub.core.audio.CaptureFormat
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

/**
 * 실제 ONNX 모델로 Kotlin 파이프라인을 돌려 Python 참조 구현(spikes/s1-wakeword)의 청크별 점수와 맞춘다.
 *
 * 픽스처는 `spikes/s1-wakeword` 의 `s1.export_fixtures` 가 만든다. 양성은 합성한 "Hey Jarvis", 음성은 한국어 일상 문장이다.
 */
class OnnxPipelineParityTest {

    private lateinit var models: WakeWordModels

    @BeforeEach
    fun setUp() {
        models = OnnxWakeWordModelFactory().create(
            BundledModels.read(WakeWordModelFiles.MELSPECTROGRAM),
            BundledModels.read(WakeWordModelFiles.EMBEDDING),
            BundledModels.read(WakeWordModelFiles.KEYWORD),
        )
    }

    @AfterEach
    fun tearDown() = models.close()

    private fun resource(name: String): ByteArray = File("src/test/resources/wakeword/$name").readBytes()

    private fun pcm(name: String): ShortArray {
        val buffer = ByteBuffer.wrap(resource("$name.pcm")).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        return ShortArray(buffer.remaining()).also { buffer.get(it) }
    }

    private fun referenceScores(name: String): List<Float> {
        val json = String(resource("reference_scores.json"))
        val block = json.substringAfter("\"$name\"").substringAfter("\"scores\": [").substringBefore("]")
        return block.split(",").map { it.trim().toFloat() }
    }

    /** 캡처와 같은 512 샘플 프레임으로 잘라 넣고, 참조 구현처럼 마지막 청크는 0 으로 채운다. */
    private fun score(name: String): List<Float> {
        val samples = pcm(name)
        val chunk = OpenWakeWordFormat.CHUNK_SAMPLES
        val padded = samples.copyOf((samples.size + chunk - 1) / chunk * chunk)
        val pipeline = WakeWordPipeline(models, WakeWordDetector(consecutiveHits = 2, refractoryMs = 2_000, threshold = 0.5f))
        val scores = mutableListOf<Float>()
        var offset = 0
        while (offset < padded.size) {
            val end = minOf(offset + CaptureFormat.FRAME_SAMPLES, padded.size)
            pipeline.feed(padded.copyOfRange(offset, end)) { scores += it.score }
            offset = end
        }
        return scores
    }

    @Test
    fun `positive clip scores match the Python reference and cross the default threshold`() {
        val actual = score("positive")
        val expected = referenceScores("positive")

        assertEquals(expected.size, actual.size)
        expected.indices.forEach { assertEquals(expected[it], actual[it], TOLERANCE, "chunk $it") }
        assertTrue(actual.max() > 0.9f)
    }

    @Test
    fun `negative clip scores match the Python reference and stay below the default threshold`() {
        val actual = score("negative")
        val expected = referenceScores("negative")

        assertEquals(expected.size, actual.size)
        expected.indices.forEach { assertEquals(expected[it], actual[it], TOLERANCE, "chunk $it") }
        assertTrue(actual.max() < 0.5f)
    }

    private companion object {
        const val TOLERANCE = 2e-3f
    }
}
