"""연결 하나의 음성 세션: 발화 수신, STT, 에이전트, 응답 스트리밍, 취소."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from cozy.v1.common_pb2 import (
    ERROR_CODE_BAD_REQUEST,
    ERROR_CODE_INTERNAL,
    ERROR_CODE_UPSTREAM_UNAVAILABLE,
)
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import (
    AUDIO_ENCODING_PCM_S16LE,
    STREAM_KIND_UTTERANCE_UP,
    AssistantState,
    AudioChunk,
    TextQuery,
    Transcript,
    UtteranceCancel,
    UtteranceEnd,
    UtteranceStart,
)
from loguru import logger

from app.config import Settings
from app.core.connections import ClientConnection
from app.core.exceptions import BadRequestError
from app.core.i18n import Translator
from app.core.openai_gateway import OpenAIGateway, OpenAIGatewayError
from app.core.protocol import error_envelope
from app.domain.agent.service import AgentService
from app.domain.voice.audio import PCM_CHANNELS, PCM_SAMPLE_WIDTH_BYTES, max_pcm_bytes, pcm_to_wav
from app.domain.voice.responder import ResponseStreamer

KEY_UNAVAILABLE = "voice.unavailable"
KEY_NOT_HEARD = "voice.not_heard"
KEY_STREAM_INVALID = "voice.stream_invalid"
KEY_TOO_LONG = "voice.utterance_too_long"
KEY_INTERNAL = "error.internal"
MIN_SAMPLE_RATE_HZ = 8000
MAX_SAMPLE_RATE_HZ = 48000

State = AssistantState.State


@dataclass(frozen=True, slots=True)
class VoiceDeps:
    """세션이 쓰는 앱 수준 의존성."""

    settings: Settings
    translate: Translator
    agent: AgentService
    openai: OpenAIGateway | None
    hub_area: Callable[[str], Awaitable[str]]
    resolve_hub: Callable[[ClientConnection], str]


@dataclass(slots=True)
class _Utterance:
    stream_id: str
    sample_rate_hz: int
    area_id: str
    buffer: bytearray = field(default_factory=bytearray)
    next_index: int = 0
    ended_at: float = 0.0


async def _single(text: str) -> AsyncIterator[str]:
    yield text


class VoiceSession:
    """연결 하나의 발화 상태. 프레임은 순서대로 들어오지만 처리는 백그라운드 태스크에서 돈다.

    메시지 루프를 막지 않아야 UtteranceCancel 과 Ping 을 처리할 수 있다.
    """

    def __init__(self, conn: ClientConnection, deps: VoiceDeps) -> None:
        self._conn = conn
        self._deps = deps
        self._utterance: _Utterance | None = None
        self._ignored_stream = ""
        self._task: asyncio.Task[None] | None = None
        self._task_stream = ""
        self._text_tasks: set[asyncio.Task[None]] = set()

    # --- 수신 -----------------------------------------------------------------------------

    async def handle(self, envelope: Envelope) -> None:
        """허브의 발화 관련 메시지 하나를 처리한다.

        Raises:
            BadRequestError: 알 수 없는 스트림이거나 허용되지 않는 청크 종류일 때.
        """
        body = envelope.WhichOneof("body")
        if body == "utterance_start":
            await self._on_start(envelope.utterance_start)
        elif body == "audio_chunk":
            await self._on_chunk(envelope.audio_chunk)
        elif body == "utterance_end":
            await self._on_end(envelope.utterance_end)
        elif body == "utterance_cancel":
            await self._on_cancel(envelope.utterance_cancel)

    async def handle_text_query(self, query: TextQuery) -> None:
        """TextQuery 를 백그라운드로 처리한다. 응답은 자막(ResponseText)만 보낸다."""
        if not query.text.strip():
            raise BadRequestError("Empty text query")
        if self._deps.openai is None:
            await self._send_quiet(
                error_envelope(
                    ERROR_CODE_UPSTREAM_UNAVAILABLE, KEY_UNAVAILABLE, request_id=query.request_id
                )
            )
            return
        task = asyncio.create_task(self._run_text(query), name="voice-text-query")
        self._text_tasks.add(task)
        task.add_done_callback(self._text_tasks.discard)

    async def close(self) -> None:
        """연결이 끊겼을 때 진행 중인 작업을 모두 취소한다."""
        self._utterance = None
        await self._cancel_task()
        tasks = list(self._text_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _on_start(self, start: UtteranceStart) -> None:
        # 새 발화는 진행 중이던 발화·응답을 끊는 끼어들기로 본다.
        await self._cancel_task()
        self._utterance = None
        if self._deps.openai is None:
            await self._reject(start.stream_id, ERROR_CODE_UPSTREAM_UNAVAILABLE, KEY_UNAVAILABLE)
            return
        fmt = start.format
        valid = (
            bool(start.stream_id)
            and fmt.encoding == AUDIO_ENCODING_PCM_S16LE
            and fmt.channels == PCM_CHANNELS
            and MIN_SAMPLE_RATE_HZ <= fmt.sample_rate_hz <= MAX_SAMPLE_RATE_HZ
        )
        if not valid:
            await self._reject(start.stream_id, ERROR_CODE_BAD_REQUEST, KEY_STREAM_INVALID)
            return
        self._ignored_stream = ""
        self._utterance = _Utterance(start.stream_id, fmt.sample_rate_hz, start.area_id)
        await self._send_state(State.STATE_LISTENING, start.stream_id)

    async def _on_chunk(self, chunk: AudioChunk) -> None:
        if chunk.kind != STREAM_KIND_UTTERANCE_UP:
            raise BadRequestError("Hub may only send UTTERANCE_UP audio")
        utt = self._utterance
        if utt is None or chunk.stream_id != utt.stream_id:
            if chunk.stream_id == self._ignored_stream:
                return
            raise BadRequestError(f"Audio chunk for unknown stream: {chunk.stream_id}")
        if chunk.index != utt.next_index or len(chunk.data) % PCM_SAMPLE_WIDTH_BYTES:
            logger.warning(
                "Invalid audio chunk: stream={} expected_index={} index={} size={}",
                utt.stream_id,
                utt.next_index,
                chunk.index,
                len(chunk.data),
            )
            await self._abort(utt, KEY_STREAM_INVALID)
            return
        limit = max_pcm_bytes(utt.sample_rate_hz, self._deps.settings.max_utterance_seconds)
        if len(utt.buffer) + len(chunk.data) > limit:
            logger.warning("Utterance too long, rejected: stream={}", utt.stream_id)
            await self._abort(utt, KEY_TOO_LONG)
            return
        utt.buffer.extend(chunk.data)
        utt.next_index += 1

    async def _on_end(self, end: UtteranceEnd) -> None:
        utt = self._utterance
        if utt is None or end.stream_id != utt.stream_id:
            if end.stream_id == self._ignored_stream:
                return
            raise BadRequestError(f"Utterance end for unknown stream: {end.stream_id}")
        self._utterance = None
        utt.ended_at = time.monotonic()
        await self._send_state(State.STATE_THINKING, utt.stream_id)
        self._task_stream = utt.stream_id
        self._task = asyncio.create_task(self._process(utt), name="voice-utterance")

    async def _on_cancel(self, cancel: UtteranceCancel) -> None:
        cancelled = False
        if self._utterance is not None and self._utterance.stream_id == cancel.stream_id:
            self._utterance = None
            cancelled = True
        task = self._task
        if task is not None and not task.done() and self._task_stream == cancel.stream_id:
            await self._cancel_task()
            cancelled = True
        if not cancelled:
            logger.debug("Cancel for unknown stream ignored: {}", cancel.stream_id)
            return
        logger.info("Utterance cancelled: stream={} reason={}", cancel.stream_id, cancel.reason)
        await self._send_state(State.STATE_IDLE, cancel.stream_id)

    async def _reject(self, stream_id: str, code: int, key: str) -> None:
        self._ignored_stream = stream_id
        await self._send_quiet(error_envelope(code, key, stream_id=stream_id))  # type: ignore[arg-type]

    async def _abort(self, utt: _Utterance, key: str) -> None:
        self._utterance = None
        await self._reject(utt.stream_id, ERROR_CODE_BAD_REQUEST, key)
        await self._send_state(State.STATE_IDLE, utt.stream_id)

    async def _cancel_task(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # --- 처리 -----------------------------------------------------------------------------

    async def _process(self, utt: _Utterance) -> None:
        try:
            await self._pipeline(utt)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Voice pipeline failed: stream={}", utt.stream_id)
            await self._send_quiet(
                error_envelope(ERROR_CODE_INTERNAL, KEY_INTERNAL, stream_id=utt.stream_id)
            )
        await self._send_state(State.STATE_IDLE, utt.stream_id)

    async def _pipeline(self, utt: _Utterance) -> None:
        deps = self._deps
        started = time.monotonic()
        transcript = await self._transcribe(utt)
        stt_ms = int((time.monotonic() - started) * 1000)
        speech = deps.openai
        if transcript is None:
            # 클라우드 연결 실패: 자막으로만 알리고 터치 제어를 안내한다.
            reply, speech = _single(deps.translate(KEY_UNAVAILABLE)), None
        elif not transcript.strip():
            reply = _single(deps.translate(KEY_NOT_HEARD))
        else:
            await self._send(
                Envelope(
                    transcript=Transcript(stream_id=utt.stream_id, text=transcript, final=True)
                )
            )
            hub_id = deps.resolve_hub(self._conn)
            area_id = utt.area_id or await deps.hub_area(hub_id)
            reply = deps.agent.handle(transcript, hub_id, area_id)
        streamer = ResponseStreamer(self._send, utt.stream_id, speech)
        await streamer.run(reply)
        first_audio = streamer.first_audio_at
        logger.info(
            "Utterance done: stream={} stt_ms={} end_to_first_audio_ms={}",
            utt.stream_id,
            stt_ms,
            None if first_audio is None else int((first_audio - utt.ended_at) * 1000),
        )

    async def _transcribe(self, utt: _Utterance) -> str | None:
        """STT 결과 텍스트. 호출이 실패하면 None."""
        gateway = self._deps.openai
        if gateway is None or not utt.buffer:
            return ""
        wav = pcm_to_wav(bytes(utt.buffer), utt.sample_rate_hz)
        try:
            return await gateway.transcribe(wav, self._deps.agent.stt_prompt())
        except OpenAIGatewayError as exc:
            logger.warning("STT failed: {}", exc)
            return None

    async def _run_text(self, query: TextQuery) -> None:
        deps = self._deps
        try:
            hub_id = deps.resolve_hub(self._conn)
            area_id = query.area_id or await deps.hub_area(hub_id)
            reply = deps.agent.handle(query.text, hub_id, area_id)
            await ResponseStreamer(self._send, query.request_id, None).run(reply)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Text query failed: request={}", query.request_id)
            await self._send_quiet(
                error_envelope(ERROR_CODE_INTERNAL, KEY_INTERNAL, request_id=query.request_id)
            )

    # --- 송신 -----------------------------------------------------------------------------

    async def _send(self, envelope: Envelope) -> None:
        await self._conn.send(envelope)

    async def _send_quiet(self, envelope: Envelope) -> None:
        try:
            await self._conn.send(envelope)
        except Exception:
            logger.debug("Send failed, connection probably closed")

    async def _send_state(self, state: AssistantState.State, stream_id: str) -> None:
        await self._send_quiet(
            Envelope(assistant_state=AssistantState(state=state, stream_id=stream_id))
        )
