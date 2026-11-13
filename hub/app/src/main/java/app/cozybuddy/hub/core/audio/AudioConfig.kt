package app.cozybuddy.hub.core.audio

/** 마이크 캡처 포맷. VAD 가 16 kHz 512 샘플 프레임을 요구하므로 캡처 프레임도 같은 크기로 맞춘다. */
object CaptureFormat {
    const val SAMPLE_RATE_HZ = 16_000
    const val FRAME_SAMPLES = 512
    const val BYTES_PER_SAMPLE = 2

    /** 프레임 하나의 길이(ms). 512 / 16000 = 32 ms. */
    const val FRAME_MS = FRAME_SAMPLES * 1_000 / SAMPLE_RATE_HZ

    /** 링버퍼가 보관하는 최대 길이. 호출어 엔진이 붙을 때 필요한 pre-roll(1~2초)을 덮는다. */
    const val RING_BUFFER_MS = 2_000

    fun msToSamples(ms: Int): Int = (ms.toLong() * SAMPLE_RATE_HZ / 1_000).toInt()
}

/** 응답 재생 포맷 기본값. 실제 값은 ResponseAudioStart.format 이 우선한다. */
object PlaybackFormat {
    const val DEFAULT_SAMPLE_RATE_HZ = 24_000
}
