"""연결 하나의 음성 세션: 발화 수신, STT, 에이전트, 응답 스트리밍, 취소."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from cozy.v1.alarm_pb2 import ReminderFired
from cozy.v1.common_pb2 import (
    ERROR_CODE_BAD_REQUEST,
    ERROR_CODE_INTERNAL,
    ERROR_CODE_UPSTREAM_UNAVAILABLE,
)
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import (
    AUDIO_ENCODING_PCM_S16LE,
    DECLINE_REASON_DUPLICATE,
    STREAM_KIND_UTTERANCE_UP,
    AssistantState,
    AudioChunk,
    ResponseText,
    TextQuery,
    Transcript,
    UtteranceCancel,
    UtteranceDeclined,
    UtteranceEnd,
    UtteranceStart,
)
from loguru import logger

from app.config import Settings
from app.core.connections import ClientConnection
from app.core.exceptions import BadRequestError
from app.core.i18n import Translator
from app.core.openai_gateway import (
    OpenAIGateway,
    OpenAIGatewayError,
    OpenAIGatewayTimeout,
    UsageLimitExceeded,
)
from app.core.protocol import error_envelope
from app.core.usage_guard import BucketRegistry
from app.domain.agent.reminder_text import fired_text
from app.domain.agent.service import AgentService
from app.domain.telemetry.recorder import TurnRecorder
from app.domain.telemetry.turn import Stage, Turn, TurnPath, TurnSource
from app.domain.voice.arbiter import Ticket, UtteranceArbiter
from app.domain.voice.audio import PCM_CHANNELS, PCM_SAMPLE_WIDTH_BYTES, max_pcm_bytes, pcm_to_wav
from app.domain.voice.responder import ResponseStreamer
from app.domain.voice.tts_cache import TtsCache

KEY_UNAVAILABLE = "voice.unavailable"
KEY_NOT_HEARD = "voice.not_heard"
KEY_STREAM_INVALID = "voice.stream_invalid"
KEY_TOO_LONG = "voice.utterance_too_long"
KEY_INTERNAL = "error.internal"
KEY_RATE_LIMITED = "voice.rate_limited"
KEY_BUDGET_EXCEEDED = "voice.budget_exceeded"
KEY_TEXT_TOO_LONG = "voice.text_too_long"
# 허브가 대화 응답과 구분하려고 보는 접두어다. 허브와의 계약이므로 바꾸지 않는다.
REMINDER_STREAM_PREFIX = "reminder-"
ERR_STT_BUDGET = "stt_budget"
MAX_REMINDER_LABEL_CHARS = 100
MIN_SAMPLE_RATE_HZ = 8000
MAX_SAMPLE_RATE_HZ = 48000

State = AssistantState.State
# 매번 같은 문장이 나오는 템플릿 경로. 날씨는 수치가 달라 제외한다.
CACHEABLE_PATHS = frozenset({TurnPath.CONTEXT, TurnPath.MATCHER, TurnPath.CLARIFY})


@dataclass(frozen=True, slots=True)
class VoiceDeps:
    """세션이 쓰는 앱 수준 의존성."""

    settings: Settings
    translate: Translator
    agent: AgentService
    turns: TurnRecorder
    openai: OpenAIGateway | None
    hub_area: Callable[[str], Awaitable[str]]
    resolve_hub: Callable[[ClientConnection], str]
    tts_cache: TtsCache | None = None
    arbiter: UtteranceArbiter | None = None
    # 클라이언트(허브·앱) ID 별 속도 제한. 연결을 다시 맺어도 버킷이 유지되도록 서비스가 소유한다.
    turn_limiter: BucketRegistry | None = None
    reminder_limiter: BucketRegistry | None = None


@dataclass(slots=True)
class _Utterance:
    stream_id: str
    sample_rate_hz: int
    area_id: str
    buffer: bytearray = field(default_factory=bytearray)
    next_index: int = 0
    ended_at: float = 0.0
    ticket: Ticket | None = None


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
        self._query_task: asyncio.Task[None] | None = None
        self._query_id = ""

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
        """TextQuery 를 백그라운드로 처리한다. 응답은 자막(ResponseText)만 보낸다.

        연결당 처리 중인 질의는 하나다. 새 질의는 진행 중인 질의를 취소하고(새 발화가 이전
        발화를 끊는 것과 같다) 취소된 질의에는 빈 final 자막을 보내 화면이 멈추지 않게 한다.

        Raises:
            BadRequestError: 본문이 비었거나 `max_text_query_chars` 를 넘을 때.
        """
        if not query.text.strip():
            raise BadRequestError("Empty text query")
        if len(query.text) > self._deps.settings.max_text_query_chars:
            raise BadRequestError("Text query too long", KEY_TEXT_TOO_LONG)
        if self._deps.openai is None:
            await self._send_quiet(
                error_envelope(
                    ERROR_CODE_UPSTREAM_UNAVAILABLE, KEY_UNAVAILABLE, request_id=query.request_id
                )
            )
            return
        if not self._take_turn_token():
            logger.warning("Text query rate limited: client={}", self._conn.client_id)
            await self._reply_subtitle(query.request_id, KEY_RATE_LIMITED)
            return
        await self._cancel_text_query()
        task = asyncio.create_task(self._run_text(query), name="voice-text-query")
        self._query_task, self._query_id = task, query.request_id
        self._text_tasks.add(task)
        task.add_done_callback(self._text_tasks.discard)

    async def handle_reminder(self, fired: ReminderFired) -> None:
        """허브가 알린 리마인더를 읽어 주는 음성 응답(RESPONSE_DOWN)을 백그라운드로 보낸다.

        Args:
            fired: 울린 리마인더. label 이 알릴 내용이다.

        Raises:
            BadRequestError: alarm_id 나 label 이 비어 있을 때.
        """
        label = fired.label.strip()[:MAX_REMINDER_LABEL_CHARS]
        if not fired.alarm_id or not label:
            raise BadRequestError("Invalid reminder")
        limiter = self._deps.reminder_limiter
        if limiter is not None and not limiter.try_take(self._conn.client_id):
            # 허브가 보낸 것이라 사용자에게 보일 응답이 없다. 합성하지 않고 버린다.
            logger.warning(
                "Reminder rate limited, dropped: hub={} alarm={}",
                self._conn.client_id,
                fired.alarm_id,
            )
            return
        task = asyncio.create_task(self._run_reminder(fired.alarm_id, label), name="voice-reminder")
        self._text_tasks.add(task)
        task.add_done_callback(self._text_tasks.discard)

    async def _run_reminder(self, alarm_id: str, label: str) -> None:
        deps = self._deps
        text = fired_text(label, deps.translate)
        stream_id = f"{REMINDER_STREAM_PREFIX}{alarm_id}"
        try:
            streamer = ResponseStreamer(
                self._send,
                stream_id,
                deps.openai,
                announce_speaking=False,
                cache=deps.tts_cache,
                cacheable=lambda: True,
            )
            await streamer.run(_single(text))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder announcement failed: alarm={}", alarm_id)

    async def close(self) -> None:
        """연결이 끊겼을 때 진행 중인 작업을 모두 취소한다."""
        self._utterance = None
        await self._cancel_task()
        self._query_task = None
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
        if not self._take_turn_token():
            logger.warning("Utterance rate limited: stream={}", utt.stream_id)
            await self._reply_subtitle(utt.stream_id, KEY_RATE_LIMITED)
            await self._send_state(State.STATE_IDLE, utt.stream_id)
            return
        utt.ended_at = time.monotonic()
        await self._send_state(State.STATE_THINKING, utt.stream_id)
        self._task_stream = utt.stream_id
        arbiter = self._deps.arbiter
        if arbiter is not None:
            utt.ticket = arbiter.begin(self._deps.resolve_hub(self._conn), utt.ended_at)
        task = self._task = asyncio.create_task(self._process(utt), name="voice-utterance")
        if arbiter is not None and utt.ticket is not None:
            # 태스크가 시작도 못 하고 취소돼도 기다리는 다른 허브가 풀리게 한다.
            ticket = utt.ticket
            task.add_done_callback(lambda _: arbiter.abandon(ticket))

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

    def _take_turn_token(self) -> bool:
        limiter = self._deps.turn_limiter
        return limiter is None or limiter.try_take(self._conn.client_id)

    async def _reply_subtitle(self, stream_id: str, key: str) -> None:
        """음성 합성 없이 자막으로만 고정 문구를 보낸다. 한도에 걸린 요청은 비용을 쓰지 않는다."""
        try:
            await ResponseStreamer(self._send, stream_id, None).run(
                _single(self._deps.translate(key))
            )
        except Exception:
            logger.debug("Subtitle reply failed, connection probably closed")

    async def _cancel_text_query(self) -> None:
        task, request_id = self._query_task, self._query_id
        self._query_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await self._send_quiet(
            Envelope(response_text=ResponseText(stream_id=request_id, delta="", final=True))
        )

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
        turn = self._deps.turns.begin(
            self._deps.resolve_hub(self._conn), TurnSource.VOICE, started_at=utt.ended_at
        )
        try:
            with self._turn_guard():
                await self._pipeline(utt, turn)
        except asyncio.CancelledError:
            turn.fail("cancelled")
            raise
        except Exception:
            logger.exception("Voice pipeline failed: stream={}", utt.stream_id)
            turn.path = TurnPath.ERROR
            turn.fail("pipeline_error")
            await self._send_quiet(
                error_envelope(ERROR_CODE_INTERNAL, KEY_INTERNAL, stream_id=utt.stream_id)
            )
        finally:
            self._deps.turns.finish(turn)
        await self._send_state(State.STATE_IDLE, utt.stream_id)

    async def _pipeline(self, utt: _Utterance, turn: Turn) -> None:
        deps = self._deps
        started = time.monotonic()
        transcript = await self._transcribe(utt, turn)
        turn.add_seconds(Stage.STT, time.monotonic() - started)
        speech = deps.openai
        if transcript is None:
            # 클라우드 연결 실패: 자막으로만 알리고 터치 제어를 안내한다.
            turn.path = TurnPath.ERROR
            turn.fail("stt_failed")
            key = KEY_BUDGET_EXCEEDED if turn.error_code == ERR_STT_BUDGET else KEY_UNAVAILABLE
            reply, speech = self._canned(turn, key), None
        elif not transcript.strip():
            turn.path = TurnPath.CLARIFY
            reply = self._canned(turn, KEY_NOT_HEARD)
        elif await self._declined_as_duplicate(utt, turn, transcript):
            return
        else:
            await self._send(
                Envelope(
                    transcript=Transcript(stream_id=utt.stream_id, text=transcript, final=True)
                )
            )
            turn.transcript = transcript
            hub_id = deps.resolve_hub(self._conn)
            area_id = utt.area_id or await deps.hub_area(hub_id)
            reply = deps.agent.handle(transcript, hub_id, area_id, turn)
        streamer = ResponseStreamer(
            self._send,
            utt.stream_id,
            speech,
            cache=deps.tts_cache,
            cacheable=lambda: self._cacheable(turn),
        )
        await streamer.run(reply)
        turn.tts_cache_hit = streamer.first_sentence_cache_hit
        first_audio = streamer.first_audio_at
        if first_audio is not None:
            turn.mark_first_audio(first_audio)
        logger.info(
            "Utterance done: stream={} stt_ms={} end_to_first_audio_ms={}",
            utt.stream_id,
            turn.t_stt_ms,
            turn.t_first_audio_ms,
        )

    async def _declined_as_duplicate(self, utt: _Utterance, turn: Turn, transcript: str) -> bool:
        """다른 허브가 같은 발화를 먼저 처리했으면 거절 통지를 보내고 True."""
        arbiter = self._deps.arbiter
        if arbiter is None or utt.ticket is None:
            return False
        started = time.monotonic()
        decision = await arbiter.decide(utt.ticket, transcript)
        held = time.monotonic() - started
        if held > 0.001:
            logger.debug("Arbitration held: stream={} held_ms={}", utt.stream_id, round(held * 1e3))
        if decision.winner:
            return False
        turn.transcript = transcript
        turn.path = TurnPath.DECLINED
        await self._send(
            Envelope(
                utterance_declined=UtteranceDeclined(
                    stream_id=utt.stream_id,
                    reason=DECLINE_REASON_DUPLICATE,
                    winner_hub_id=decision.winner_hub_id,
                )
            )
        )
        return True

    def _turn_guard(self) -> contextlib.AbstractContextManager[None]:
        cache = self._deps.tts_cache
        return cache.turn_active() if cache is not None else contextlib.nullcontext()

    def _cacheable(self, turn: Turn) -> bool:
        """결정론 템플릿 응답만 캐시한다. LLM 문장은 설정이 켜졌을 때만."""
        if turn.path in CACHEABLE_PATHS:
            return True
        return turn.path is TurnPath.LLM and self._deps.settings.tts_cache_llm

    def _canned(self, turn: Turn, key: str) -> AsyncIterator[str]:
        """에이전트를 거치지 않는 고정 응답. 턴 기록에도 응답을 남긴다."""
        text = self._deps.translate(key)
        turn.append_response(text)
        return _single(text)

    async def _transcribe(self, utt: _Utterance, turn: Turn) -> str | None:
        """STT 결과 텍스트. 호출이 실패하거나 제한 시간을 넘기면 None."""
        gateway = self._deps.openai
        if gateway is None or not utt.buffer:
            return ""
        wav = pcm_to_wav(bytes(utt.buffer), utt.sample_rate_hz)
        try:
            return await gateway.transcribe(
                wav,
                self._deps.agent.stt_prompt(),
                on_hedge=lambda: setattr(turn, "stt_hedged", True),
            )
        except UsageLimitExceeded:
            turn.fail(ERR_STT_BUDGET)
            return None
        except OpenAIGatewayTimeout as exc:
            logger.warning("STT timed out: {}", exc)
            turn.fail("stt_timeout")
            return None
        except OpenAIGatewayError as exc:
            logger.warning("STT failed: {}", exc)
            return None

    async def _run_text(self, query: TextQuery) -> None:
        deps = self._deps
        hub_id = deps.resolve_hub(self._conn)
        turn = deps.turns.begin(hub_id, TurnSource.TEXT)
        turn.transcript = query.text
        try:
            area_id = query.area_id or await deps.hub_area(hub_id)
            reply = deps.agent.handle(query.text, hub_id, area_id, turn)
            await ResponseStreamer(self._send, query.request_id, None).run(reply)
        except asyncio.CancelledError:
            turn.fail("cancelled")
            raise
        except Exception:
            logger.exception("Text query failed: request={}", query.request_id)
            turn.path = TurnPath.ERROR
            turn.fail("pipeline_error")
            await self._send_quiet(
                error_envelope(ERROR_CODE_INTERNAL, KEY_INTERNAL, request_id=query.request_id)
            )
        finally:
            deps.turns.finish(turn)

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
