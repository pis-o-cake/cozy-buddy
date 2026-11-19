"""비용 남용 방어: 질의 길이, 동시 질의, 턴 속도 제한, 하루 예산의 WebSocket 전체 흐름."""

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest
from cozy.v1.alarm_pb2 import ReminderFired
from cozy.v1.common_pb2 import ERROR_CODE_BAD_REQUEST
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.session_pb2 import Ping
from cozy.v1.voice_pb2 import TextQuery
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.core.i18n import Translator
from app.core.openai_gateway import ChatChunk, charge_llm, charge_stt, charge_tts
from app.core.usage_guard import UsageGuard, UsageKind
from tests.conftest import connect_hub, connect_mobile, eventually, send
from tests.fake_ha import FakeHomeAssistant
from tests.test_voice import _build_client
from tests.voice_support import (
    FakeOpenAI,
    is_final_text,
    is_idle,
    make_korean_home,
    of_body,
    recv_until,
    send_chunk,
    send_end,
    send_utterance,
    send_utterance_start,
    spoken_text,
    text_turn,
)

translate = Translator("ko")
RATE_LIMITED = translate("voice.rate_limited")
BUDGET_EXCEEDED = translate("voice.budget_exceeded")
MATCHER_PHRASE = "거실 조명 꺼줘"


class BudgetedFake(FakeOpenAI):
    """실제 게이트웨이와 같은 예산 규칙(charge_*)을 거치는 가짜."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.usage = UsageGuard(settings)
        self.chat_block: asyncio.Event | None = None

    async def transcribe(self, wav: bytes, prompt: str | None = None, on_hedge: Any = None) -> str:
        charge_stt(self.usage, wav)
        return await super().transcribe(wav, prompt, on_hedge)

    async def stream_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        charge_llm(self.usage)
        if self.chat_block is not None:
            await self.chat_block.wait()
        async for chunk in super().stream_chat(messages, tools):
            yield chunk

    async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
        charge_tts(self.usage, text)
        async for pcm in super().stream_speech(text):
            yield pcm


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    fake.start()
    yield fake
    fake.close()


@contextmanager
def build(ha_settings: Settings, **update: Any) -> Iterator[tuple[TestClient, BudgetedFake]]:
    settings = ha_settings.model_copy(update={"max_utterance_seconds": 1.0, **update})
    gateway = BudgetedFake(settings)
    app = _build_client(settings, gateway)
    client = next(app)
    try:
        yield client, gateway
    finally:
        with suppress(StopIteration):
            next(app)


def ask(ws: WebSocketTestSession, text: str, request_id: str = "q1") -> list[Envelope]:
    send(ws, Envelope(text_query=TextQuery(request_id=request_id, text=text)))
    return recv_until(ws, is_final_text)


# --- 질의 길이와 동시성 -----------------------------------------------------------------


def test_oversized_text_query_gets_bad_request_error_frame(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with (
        build(ha_settings, max_text_query_chars=20) as (client, gateway),
        connect_mobile(client) as ws,
    ):
        send(ws, Envelope(text_query=TextQuery(request_id="big", text="가" * 21)))
        error = recv_until(ws, lambda e: e.WhichOneof("body") == "error")[-1].error
        assert error.code == ERROR_CODE_BAD_REQUEST
        assert error.message_key == "voice.text_too_long"
        assert error.request_id == "big"
        gateway.chat_turns.append(text_turn("통과"))
        assert spoken_text(ask(ws, "가" * 20)) == "통과"  # 한도와 같은 길이는 통과


def test_new_text_query_cancels_the_one_in_flight(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with build(ha_settings) as (client, gateway):
        gateway.chat_block = asyncio.Event()
        with connect_mobile(client) as ws:
            send(ws, Envelope(text_query=TextQuery(request_id="q1", text="안녕 뭐해")))
            eventually(lambda: gateway.usage.snapshot().used[UsageKind.LLM_CALLS] == 1)
            gateway.chat_block = None
            gateway.chat_turns.append(text_turn("둘째 답"))
            send(ws, Envelope(text_query=TextQuery(request_id="q2", text="안녕 또 뭐해")))
            frames = recv_until(
                ws, lambda e: is_final_text(e) and e.response_text.stream_id == "q2"
            )
    first = [f for f in frames if f.response_text.stream_id == "q1"]
    assert first and first[0].response_text.final and first[0].response_text.delta == ""
    assert spoken_text([f for f in frames if f.response_text.stream_id == "q2"]) == "둘째 답"


# --- 턴 속도 제한 -----------------------------------------------------------------------


def test_text_queries_beyond_rate_limit_get_localized_reply(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with (
        build(ha_settings, turn_rate_per_minute=3) as (client, gateway),
        connect_mobile(client) as ws,
    ):
        replies = [spoken_text(ask(ws, MATCHER_PHRASE, f"q{i}")) for i in range(5)]
    assert replies[:3] == ["거실 조명을 껐어요"] * 3
    assert replies[3:] == [RATE_LIMITED] * 2
    assert RATE_LIMITED == "잠시 후에 다시 말해 주세요."
    assert gateway.chat_calls == []


def test_utterance_over_rate_limit_skips_stt_and_tts(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with (
        build(ha_settings, turn_rate_per_minute=1) as (client, gateway),
        connect_hub(client) as hub,
    ):
        send_utterance(hub, "u1")
        recv_until(hub, is_idle)
        tts_before = len(gateway.tts_texts)
        send_utterance(hub, "u2")
        frames = recv_until(hub, is_idle)
    assert spoken_text(frames) == RATE_LIMITED
    assert len(gateway.stt_calls) == 1
    assert len(gateway.tts_texts) == tts_before
    assert not of_body(frames, "response_audio_start")


def test_rate_limit_is_per_client(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with (
        build(ha_settings, turn_rate_per_minute=1) as (client, _),
        connect_mobile(client) as mobile,
        connect_hub(client) as hub,
    ):
        assert spoken_text(ask(mobile, MATCHER_PHRASE, "m1")) == "거실 조명을 껐어요"
        assert spoken_text(ask(mobile, MATCHER_PHRASE, "m2")) == RATE_LIMITED
        send(hub, Envelope(text_query=TextQuery(request_id="h1", text=MATCHER_PHRASE)))
        assert spoken_text(recv_until(hub, is_final_text)) == "거실 조명을 껐어요"


def test_reminders_are_rate_limited_per_hub(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with (
        build(ha_settings, reminder_rate_per_minute=2) as (client, gateway),
        connect_hub(client) as hub,
    ):
        for i in range(4):
            send(hub, Envelope(reminder_fired=ReminderFired(alarm_id=f"r{i}", label="약")))
        send(hub, Envelope(ping=Ping(sent_epoch_ms=1)))
        recv_until(hub, lambda e: e.WhichOneof("body") == "pong")
        eventually(lambda: len(gateway.tts_texts) >= 2)
    assert len(gateway.tts_texts) == 2


# --- 하루 예산 --------------------------------------------------------------------------


def test_llm_budget_exhausted_refuses_llm_but_matcher_still_works(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with build(ha_settings, daily_llm_calls=1) as (client, gateway):
        gateway.chat_turns.append(text_turn("LLM 답"))
        with connect_mobile(client) as ws:
            assert spoken_text(ask(ws, "안녕 뭐해", "a")) == "LLM 답"
            assert spoken_text(ask(ws, "안녕 또 뭐해", "b")) == BUDGET_EXCEEDED
            assert spoken_text(ask(ws, MATCHER_PHRASE, "c")) == "거실 조명을 껐어요"
    assert len(gateway.chat_calls) == 1


def test_stt_budget_exhausted_answers_with_budget_message(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with build(ha_settings, daily_stt_seconds=1) as (client, gateway), connect_hub(client) as hub:
        for stream_id in ("u1", "u2"):
            send_utterance_start(hub, stream_id)
            send_chunk(hub, 0, size=19200, stream_id=stream_id, last=True)  # 0.6 s
            send_end(hub, stream_id)
            frames = recv_until(hub, is_idle)
    assert spoken_text(frames) == BUDGET_EXCEEDED
    assert len(gateway.stt_calls) == 1


def test_tts_budget_exhausted_leaves_subtitles_only(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    with build(ha_settings, daily_tts_chars=5) as (client, gateway), connect_hub(client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)
    assert spoken_text(frames) == "거실 조명을 껐어요"
    assert not of_body(frames, "response_audio_start")
    assert gateway.tts_texts == []


def test_cached_tts_hits_do_not_count_toward_budget(
    ha_settings: Settings, fake_ha: FakeHomeAssistant, tmp_path: Path
) -> None:
    sentence = "거실 조명을 껐어요"
    with build(
        ha_settings,
        tts_cache_enabled=True,
        tts_cache_dir=str(tmp_path / "cache"),
        daily_tts_chars=len(sentence) + 2,
    ) as (client, gateway):
        with connect_hub(client) as hub:
            send_utterance(hub, "u1")
            recv_until(hub, is_idle)
            used = gateway.usage.snapshot().used[UsageKind.TTS_CHARS]
            send_utterance(hub, "u2")
            frames = recv_until(hub, is_idle)
        assert gateway.usage.snapshot().used[UsageKind.TTS_CHARS] == used
    assert used == len(sentence)
    assert of_body(frames, "response_audio_start")
    assert len(gateway.tts_texts) == 1
