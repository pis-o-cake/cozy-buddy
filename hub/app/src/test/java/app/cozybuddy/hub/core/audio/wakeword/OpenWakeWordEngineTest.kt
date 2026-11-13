package app.cozybuddy.hub.core.audio.wakeword

import app.cozybuddy.hub.core.audio.CaptureFormat
import java.io.File
import java.util.concurrent.Executors
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** 번들 모델 파일. Gradle 이 모듈 디렉터리를 작업 디렉터리로 쓴다. */
internal object BundledModels {
    private val dir = File("src/main/assets/${WakeWordModelFiles.ASSET_DIR}")

    fun read(name: String): ByteArray = File(dir, name).readBytes()
}

class OpenWakeWordEngineTest {

    private val executor = Executors.newSingleThreadExecutor { Thread(it, "wakeword-test") }

    @AfterEach
    fun tearDown() {
        executor.shutdownNow()
    }

    private fun engine(
        store: WakeWordModelStore = WakeWordModelStore { BundledModels.read(it) },
        models: WakeWordModels = FakeWakeWordModels(List(100) { 0.95f }),
        factory: WakeWordModelFactory = WakeWordModelFactory { _, _, _ -> models },
    ) = OpenWakeWordEngine(store, factory, WakeWordConfig(), executor)

    private fun frame() = ShortArray(CaptureFormat.FRAME_SAMPLES)

    @Test
    fun `bundled model files match the recorded checksums`() {
        for (name in WakeWordModelFiles.expectedHashes.keys) {
            assertTrue(WakeWordModelFiles.matches(name, BundledModels.read(name)), name)
        }
    }

    @Test
    fun `initialize succeeds with intact models`() = runBlocking {
        val engine = engine()

        assertTrue(engine.initialize())
        assertEquals(WakeWordStatus.Ready, engine.status.value)
    }

    @Test
    fun `missing model file reports MODEL_MISSING instead of throwing`() = runBlocking {
        val engine = engine(store = WakeWordModelStore { if (it == WakeWordModelFiles.KEYWORD) null else BundledModels.read(it) })

        assertFalse(engine.initialize())
        assertEquals(WakeWordStatus.Failed(WakeWordFailure.MODEL_MISSING), engine.status.value)
        assertEquals(0f, engine.process(frame()))
    }

    @Test
    fun `tampered model file reports MODEL_CORRUPT`() = runBlocking {
        val engine = engine(
            store = WakeWordModelStore {
                BundledModels.read(it).also { bytes -> if (it == WakeWordModelFiles.EMBEDDING) bytes[0] = (bytes[0] + 1).toByte() }
            },
        )

        assertFalse(engine.initialize())
        assertEquals(WakeWordStatus.Failed(WakeWordFailure.MODEL_CORRUPT), engine.status.value)
    }

    @Test
    fun `runtime that cannot open the models reports RUNTIME`() = runBlocking {
        val engine = engine(factory = WakeWordModelFactory { _, _, _ -> error("no ORT") })

        assertFalse(engine.initialize())
        assertEquals(WakeWordStatus.Failed(WakeWordFailure.RUNTIME), engine.status.value)
    }

    @Test
    fun `sustained high scores emit one event and the refractory period blocks repeats`() = runBlocking {
        val engine = engine()
        engine.initialize()
        val events = mutableListOf<WakeEvent>()
        val collector = launch(start = CoroutineStart.UNDISPATCHED) { engine.events.collect { events += it } }

        // 80 ms 청크 20 개 = 1.6 s. 첫 감지 후 2 s 불응이라 한 번만 나온다.
        repeat(CHUNKS_IN_1_6_S * 1280 / CaptureFormat.FRAME_SAMPLES) { engine.process(frame()) }
        delay(50)
        collector.cancel()

        assertEquals(1, events.size)
        assertEquals(0.95f, events.single().score)
    }

    @Test
    fun `raising the threshold above the score suppresses detection`() = runBlocking {
        val engine = engine()
        engine.initialize()
        engine.setThreshold(0.97f)
        val events = mutableListOf<WakeEvent>()
        val collector = launch(start = CoroutineStart.UNDISPATCHED) { engine.events.collect { events += it } }

        repeat(40) { engine.process(frame()) }
        delay(50)
        collector.cancel()

        assertTrue(events.isEmpty())
    }

    @Test
    fun `reset restarts warm-up so no event fires right after it`() = runBlocking {
        val engine = engine()
        engine.initialize()
        repeat(40) { engine.process(frame()) }
        val events = mutableListOf<WakeEvent>()
        val collector = launch(start = CoroutineStart.UNDISPATCHED) { engine.events.collect { events += it } }

        engine.reset()
        // 초기화 직후에는 워밍업 5 예측 + 연속 2 회가 필요하다. 1280 샘플 x 6 = 15 프레임이면 아직 모자란다.
        repeat(15) { engine.process(frame()) }
        delay(50)
        collector.cancel()

        assertTrue(events.isEmpty())
    }

    @Test
    fun `release closes the models and returns to Idle`() = runBlocking {
        val engine = engine()
        engine.initialize()

        engine.release()
        delay(100)

        assertEquals(WakeWordStatus.Idle, engine.status.value)
        assertEquals(0f, engine.process(frame()))
    }

    private companion object {
        const val CHUNKS_IN_1_6_S = 20
    }
}
