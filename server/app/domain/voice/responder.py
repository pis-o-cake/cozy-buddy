"""응답 텍스트 스트림을 자막(ResponseText)과 음성(AudioChunk)으로 내보낸다."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import (
    AUDIO_ENCODING_PCM_S16LE,
    STREAM_KIND_RESPONSE_DOWN,
    AssistantState,
    AudioChunk,
    AudioFormat,
    ResponseAudioEnd,
    ResponseAudioStart,
    ResponseText,
)
from loguru import logger

from app.core.openai_gateway import TTS_SAMPLE_RATE_HZ, OpenAIGateway, OpenAIGatewayError
from app.domain.voice.audio import PCM_CHANNELS, RESPONSE_CHUNK_MS, PcmChunker
from app.domain.voice.tts_text import normalize_for_tts, split_sentences

SendFn = Callable[[Envelope], Awaitable[None]]


class _AudioSender:
    """문장별로 이어지는 TTS PCM 을 응답 오디오 스트림 하나로 내보낸다."""

    def __init__(self, send: SendFn, stream_id: str, text_stream_id: str, announce: bool) -> None:
        self._send = send
        self._audio_stream_id = f"{stream_id}-r"
        self._text_stream_id = text_stream_id
        self._announce = announce
        self._chunker = PcmChunker(TTS_SAMPLE_RATE_HZ, RESPONSE_CHUNK_MS)
        self._index = 0
        self.started = False
        self.first_audio_at: float | None = None

    async def write(self, pcm: bytes) -> None:
        if not self.started:
            await self._start()
        for chunk in self._chunker.feed(pcm):
            await self._send_chunk(chunk, last=False)

    async def finish(self) -> None:
        if not self.started:
            return
        await self._send_chunk(self._chunker.flush(), last=True)
        await self._send(
            Envelope(response_audio_end=ResponseAudioEnd(stream_id=self._audio_stream_id))
        )

    async def _start(self) -> None:
        self.started = True
        self.first_audio_at = time.monotonic()
        if self._announce:
            state = AssistantState(
                state=AssistantState.STATE_SPEAKING, stream_id=self._text_stream_id
            )
            await self._send(Envelope(assistant_state=state))
        start = ResponseAudioStart(
            stream_id=self._audio_stream_id,
            kind=STREAM_KIND_RESPONSE_DOWN,
            format=AudioFormat(
                encoding=AUDIO_ENCODING_PCM_S16LE,
                sample_rate_hz=TTS_SAMPLE_RATE_HZ,
                channels=PCM_CHANNELS,
            ),
        )
        await self._send(Envelope(response_audio_start=start))

    async def _send_chunk(self, data: bytes, *, last: bool) -> None:
        chunk = AudioChunk(
            kind=STREAM_KIND_RESPONSE_DOWN,
            stream_id=self._audio_stream_id,
            index=self._index,
            data=data,
            last=last,
        )
        self._index += 1
        await self._send(Envelope(audio_chunk=chunk))


class ResponseStreamer:
    """텍스트 조각을 받아 자막을 바로 보내고, 문장 단위로 TTS 를 돌려 오디오를 흘린다.

    TTS 가 없거나(gateway 가 None) 실패하면 자막만 보낸다.
    """

    def __init__(
        self,
        send: SendFn,
        stream_id: str,
        speech: OpenAIGateway | None,
        *,
        announce_speaking: bool = True,
    ) -> None:
        self._send = send
        self._stream_id = stream_id
        self._speech = speech
        self._audio = _AudioSender(send, stream_id, stream_id, announce_speaking)

    @property
    def first_audio_at(self) -> float | None:
        """첫 오디오 조각을 보낸 monotonic 시각. 오디오가 없으면 None."""
        return self._audio.first_audio_at

    async def run(self, deltas: AsyncIterator[str]) -> None:
        """텍스트 스트림을 끝까지 내보낸다. 취소되면 진행 중인 TTS 도 함께 취소한다."""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = (
            asyncio.create_task(self._speak(queue), name="voice-tts") if self._speech else None
        )
        try:
            await self._pump(deltas, queue)
            await self._send_text("", final=True)
            if speaker is not None:
                await speaker
        finally:
            if speaker is not None and not speaker.done():
                speaker.cancel()
                await asyncio.gather(speaker, return_exceptions=True)

    async def _pump(self, deltas: AsyncIterator[str], queue: asyncio.Queue[str | None]) -> None:
        buffer = ""
        async for delta in deltas:
            if not delta:
                continue
            await self._send_text(delta, final=False)
            buffer += delta
            sentences, buffer = split_sentences(buffer, final=False)
            for sentence in sentences:
                queue.put_nowait(sentence)
        for sentence in split_sentences(buffer, final=True)[0]:
            queue.put_nowait(sentence)
        queue.put_nowait(None)

    async def _send_text(self, delta: str, *, final: bool) -> None:
        text = ResponseText(stream_id=self._stream_id, delta=delta, final=final)
        await self._send(Envelope(response_text=text))

    async def _speak(self, queue: asyncio.Queue[str | None]) -> None:
        assert self._speech is not None
        failed = False
        while (sentence := await queue.get()) is not None:
            if failed:
                continue
            spoken = normalize_for_tts(sentence)
            if not spoken:
                continue
            try:
                async for pcm in self._speech.stream_speech(spoken):
                    await self._audio.write(pcm)
            except OpenAIGatewayError as exc:
                # 한 문장이라도 실패하면 이후 문장은 합성하지 않고 자막만 남긴다.
                logger.warning("TTS failed, continuing with subtitles only: {}", exc)
                failed = True
        await self._audio.finish()
