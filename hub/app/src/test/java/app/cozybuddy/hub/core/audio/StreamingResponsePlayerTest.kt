package app.cozybuddy.hub.core.audio

import app.cozybuddy.protocol.v1.StreamKind
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

private class FakeSink : PcmSink {
    var playCalled = false
    var released = false
    val bytes = ByteArrayOutputStream()
    val writeSizes = mutableListOf<Int>()

    override fun play() {
        playCalled = true
    }

    override fun write(data: ByteArray, offset: Int, length: Int): Int {
        if (released) return -1
        bytes.write(data, offset, length)
        writeSizes += length
        return length
    }

    override fun playedFrames(): Long = bytes.size() / 2L

    override fun release() {
        released = true
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class StreamingResponsePlayerTest {

    private val sinks = mutableListOf<FakeSink>()
    private val rates = mutableListOf<Int>()

    private fun TestScope.player(jitterMs: Int = 120) = StreamingResponsePlayer(
        sinkFactory = PcmSinkFactory { rate ->
            rates += rate
            FakeSink().also { sinks += it }
        },
        dispatcher = StandardTestDispatcher(testScheduler),
        jitterMs = jitterMs,
    )

    private fun TestScope.collectDrained(player: StreamingResponsePlayer): List<PlaybackDrained> {
        val events = mutableListOf<PlaybackDrained>()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { player.drained.toList(events) }
        return events
    }

    // 24 kHz PCM16 에서 jitter 120 ms = 5760 바이트.
    private fun chunk(size: Int, fill: Int = 1) = ByteArray(size) { fill.toByte() }

    @Test
    fun `playback waits for the jitter buffer before starting`() = runTest {
        val player = player()
        player.start("r1", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("r1", 0, chunk(2_000))
        runCurrent()
        assertFalse(sinks.single().playCalled)
        assertEquals(0, sinks.single().bytes.size())

        player.write("r1", 1, chunk(4_000, fill = 2))
        runCurrent()

        val sink = sinks.single()
        assertTrue(sink.playCalled)
        assertEquals(6_000, sink.bytes.size())
        assertEquals(listOf(2_000, 4_000), sink.writeSizes)
        assertEquals(listOf(24_000), rates)
    }

    @Test
    fun `chunks after the jitter threshold are written as they arrive`() = runTest {
        val player = player(jitterMs = 10)
        player.start("r1", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("r1", 0, chunk(1_000))
        runCurrent()
        player.write("r1", 1, chunk(500))
        runCurrent()

        assertEquals(listOf(1_000, 500), sinks.single().writeSizes)
    }

    @Test
    fun `a short stream plays on finish and reports drained with its kind`() = runTest {
        val player = player()
        val drained = collectDrained(player)
        player.start("b1", StreamKind.STREAM_KIND_BROADCAST_DOWN, 24_000)
        player.write("b1", 0, chunk(1_000))
        player.finish("b1")
        runCurrent()

        assertTrue(sinks.single().playCalled)
        assertEquals(1_000, sinks.single().bytes.size())
        assertEquals(listOf(PlaybackDrained("b1", StreamKind.STREAM_KIND_BROADCAST_DOWN)), drained)
        assertNull(player.current.value)
        assertTrue(sinks.single().released)
    }

    @Test
    fun `stop releases the sink immediately and reports nothing`() = runTest {
        val player = player(jitterMs = 10)
        val drained = collectDrained(player)
        player.start("r1", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("r1", 0, chunk(1_000))
        runCurrent()

        player.stop()

        assertTrue(sinks.single().released)
        assertNull(player.current.value)
        runCurrent()
        assertTrue(drained.isEmpty())
    }

    @Test
    fun `starting a new stream cuts the previous one`() = runTest {
        val player = player(jitterMs = 10)
        player.start("r1", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("r1", 0, chunk(1_000))
        runCurrent()

        player.start("r2", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("r1", 1, chunk(1_000, fill = 9))
        player.write("r2", 0, chunk(800, fill = 3))
        runCurrent()

        assertTrue(sinks[0].released)
        assertEquals(1_000, sinks[0].bytes.size())
        assertEquals(800, sinks[1].bytes.size())
        assertEquals("r2", player.current.value?.streamId)
    }

    @Test
    fun `stale and duplicate chunk indices are dropped`() = runTest {
        val player = player(jitterMs = 10)
        player.start("r1", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("r1", 0, chunk(100, fill = 1))
        player.write("r1", 1, chunk(100, fill = 2))
        player.write("r1", 1, chunk(100, fill = 3))
        player.write("r1", 0, chunk(100, fill = 4))
        player.finish("r1")
        runCurrent()

        val out = sinks.single().bytes.toByteArray()
        assertEquals(200, out.size)
        assertEquals(1, out[0].toInt())
        assertEquals(2, out[100].toInt())
    }

    @Test
    fun `odd sized chunks are re-aligned to whole samples`() = runTest {
        val player = player(jitterMs = 1)
        player.start("r1", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("r1", 0, chunk(3))
        player.write("r1", 1, chunk(3))
        player.finish("r1")
        runCurrent()

        assertEquals(6, sinks.single().bytes.size())
        assertTrue(sinks.single().writeSizes.all { it % 2 == 0 })
    }

    @Test
    fun `chunks for an unknown stream are ignored`() = runTest {
        val player = player(jitterMs = 1)
        player.start("r1", StreamKind.STREAM_KIND_RESPONSE_DOWN, 24_000)
        player.write("other", 0, chunk(1_000))
        runCurrent()

        assertEquals(0, sinks.single().bytes.size())
    }
}
