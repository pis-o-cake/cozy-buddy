"""PCM 오디오 유틸. 메모리 안에서만 다룬다."""

import io
import wave

PCM_SAMPLE_WIDTH_BYTES = 2
PCM_CHANNELS = 1
RESPONSE_SAMPLE_RATE_HZ = 24000
RESPONSE_CHUNK_MS = 30


def pcm_to_wav(pcm: bytes, sample_rate_hz: int) -> bytes:
    """PCM_S16LE mono 를 WAV 컨테이너로 감싼다(STT 업로드용).

    Args:
        pcm: 샘플 경계(2바이트)에 맞는 PCM 바이트.
        sample_rate_hz: 샘플레이트.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(PCM_CHANNELS)
        wav.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm)
    return buffer.getvalue()


def max_pcm_bytes(sample_rate_hz: int, seconds: float) -> int:
    """주어진 길이의 PCM_S16LE mono 바이트 수."""
    return int(sample_rate_hz * PCM_SAMPLE_WIDTH_BYTES * PCM_CHANNELS * seconds)


class PcmChunker:
    """들어오는 PCM 을 고정 크기 조각으로 다시 자른다. 샘플 경계를 지킨다."""

    def __init__(self, sample_rate_hz: int, chunk_ms: int) -> None:
        self._size = max_pcm_bytes(sample_rate_hz, chunk_ms / 1000)
        self._size -= self._size % PCM_SAMPLE_WIDTH_BYTES
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """데이터를 넣고 가득 찬 조각을 꺼낸다."""
        self._buffer.extend(data)
        chunks: list[bytes] = []
        while len(self._buffer) >= self._size:
            chunks.append(bytes(self._buffer[: self._size]))
            del self._buffer[: self._size]
        return chunks

    def flush(self) -> bytes:
        """남은 데이터를 샘플 단위로 꺼낸다. 홀수 바이트는 버린다."""
        rest = bytes(self._buffer[: len(self._buffer) - len(self._buffer) % PCM_SAMPLE_WIDTH_BYTES])
        self._buffer.clear()
        return rest
