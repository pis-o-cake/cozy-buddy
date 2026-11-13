package app.cozybuddy.hub.core.audio

import kotlinx.coroutines.channels.ClosedReceiveChannelException
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class PcmRingBufferTest {

    private fun shorts(vararg values: Int) = ShortArray(values.size) { values[it].toShort() }

    @Test
    fun `latest returns what was written in order before wrapping`() {
        val ring = PcmRingBuffer(8)
        ring.write(shorts(1, 2, 3))
        ring.write(shorts(4, 5))

        assertArrayEquals(shorts(3, 4, 5), ring.latest(3))
        assertArrayEquals(shorts(1, 2, 3, 4, 5), ring.latest(100))
        assertEquals(5, ring.size)
    }

    @Test
    fun `overflow overwrites the oldest samples and keeps time order`() {
        val ring = PcmRingBuffer(4)
        ring.write(shorts(1, 2, 3))
        ring.write(shorts(4, 5, 6))

        assertEquals(4, ring.size)
        assertArrayEquals(shorts(3, 4, 5, 6), ring.latest(4))
        assertArrayEquals(shorts(5, 6), ring.latest(2))
    }

    @Test
    fun `a single write longer than capacity keeps only the tail`() {
        val ring = PcmRingBuffer(3)
        ring.write(shorts(1, 2, 3, 4, 5))

        assertArrayEquals(shorts(3, 4, 5), ring.latest(3))
    }

    @Test
    fun `clear empties the buffer`() {
        val ring = PcmRingBuffer(4)
        ring.write(shorts(1, 2))
        ring.clear()

        assertEquals(0, ring.size)
        assertEquals(0, ring.latest(4).size)
    }

    @Test
    fun `capacity must be positive`() {
        assertThrows(IllegalArgumentException::class.java) { PcmRingBuffer(0) }
    }

    @Test
    fun `default ring holds at least 1_5 seconds of pre-roll`() {
        val distributor = FrameDistributor()
        val frame = ShortArray(CaptureFormat.FRAME_SAMPLES) { 1 }
        repeat(100) { distributor.publish(frame) }

        distributor.subscribe(1_500).use {
            assertEquals(CaptureFormat.msToSamples(1_500), it.preRoll.size)
        }
        assertTrue(CaptureFormat.msToSamples(CaptureFormat.RING_BUFFER_MS) >= CaptureFormat.msToSamples(1_500))
    }
}

class FrameDistributorTest {

    private fun frame(value: Int) = ShortArray(CaptureFormat.FRAME_SAMPLES) { value.toShort() }

    @Test
    fun `subscription gets pre-roll then continues with live frames without gaps`() {
        val distributor = FrameDistributor()
        repeat(10) { distributor.publish(frame(it + 1)) }

        val subscription = distributor.subscribe(preRollMs = CaptureFormat.FRAME_MS * 2)
        distributor.publish(frame(11))
        distributor.publish(frame(12))

        // pre-roll 은 구독 직전 두 프레임(9, 10)이고, 라이브는 그 다음 프레임(11)부터다.
        assertEquals(CaptureFormat.FRAME_SAMPLES * 2, subscription.preRoll.size)
        assertEquals(9.toShort(), subscription.preRoll.first())
        assertEquals(10.toShort(), subscription.preRoll.last())
        assertEquals(11.toShort(), subscription.live.tryReceive().getOrNull()?.first())
        assertEquals(12.toShort(), subscription.live.tryReceive().getOrNull()?.first())
        subscription.close()
    }

    @Test
    fun `pre-roll is empty when nothing was captured yet`() {
        val distributor = FrameDistributor()

        distributor.subscribe(1_000).use { assertEquals(0, it.preRoll.size) }
    }

    @Test
    fun `closed subscription stops receiving and a slow subscriber drops oldest frames`() {
        val distributor = FrameDistributor(subscriberBufferFrames = 2)
        val slow = distributor.subscribe(0)
        repeat(5) { distributor.publish(frame(it + 1)) }

        assertEquals(4.toShort(), slow.live.tryReceive().getOrNull()?.first())
        assertEquals(5.toShort(), slow.live.tryReceive().getOrNull()?.first())

        slow.close()
        distributor.publish(frame(6))
        assertTrue(slow.live.tryReceive().isClosed)
    }

    @Test
    fun `reset clears pre-roll history`() {
        val distributor = FrameDistributor()
        distributor.publish(frame(1))
        distributor.reset()

        distributor.subscribe(1_000).use { assertEquals(0, it.preRoll.size) }
    }

    @Test
    fun `closed channel rejects further receive`() {
        val distributor = FrameDistributor()
        val subscription = distributor.subscribe(0)
        subscription.close()

        assertThrows(ClosedReceiveChannelException::class.java) {
            kotlinx.coroutines.runBlocking { subscription.live.receive() }
        }
    }
}
