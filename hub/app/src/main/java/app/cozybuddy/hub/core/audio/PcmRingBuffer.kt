package app.cozybuddy.hub.core.audio

/**
 * 고정 용량 PCM 링버퍼. 가득 차면 가장 오래된 샘플부터 덮어쓴다.
 *
 * 스레드 안전하지 않다. 호출자([FrameDistributor])가 락으로 감싼다.
 */
class PcmRingBuffer(private val capacity: Int) {
    private val data = ShortArray(capacity)
    private var writePos = 0

    /** 지금까지 보관 중인 샘플 수. 최대 [capacity]. */
    var size = 0
        private set

    init {
        require(capacity > 0) { "capacity must be positive" }
    }

    fun write(samples: ShortArray) {
        // 용량보다 긴 입력은 뒤쪽 capacity 개만 의미가 있다.
        val offset = maxOf(0, samples.size - capacity)
        for (i in offset until samples.size) {
            data[writePos] = samples[i]
            writePos = (writePos + 1) % capacity
        }
        size = minOf(capacity.toLong(), size.toLong() + samples.size).toInt()
    }

    /** 가장 최근 [count] 샘플을 시간순으로 복사한다. 보관량보다 많이 요청하면 있는 만큼만 돌려준다. */
    fun latest(count: Int): ShortArray {
        val n = minOf(count.coerceAtLeast(0), size)
        val out = ShortArray(n)
        var pos = (writePos - n + capacity) % capacity
        for (i in 0 until n) {
            out[i] = data[pos]
            pos = (pos + 1) % capacity
        }
        return out
    }

    fun clear() {
        writePos = 0
        size = 0
    }
}
