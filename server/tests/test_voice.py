"""음성 파이프라인: WebSocket 전체 흐름을 가짜 OpenAIGateway 와 가짜 HA 로 검증한다."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from cozy.v1.alarm_pb2 import (
    ALARM_KIND_ALARM,
    ALARM_KIND_TIMER,
    DAY_OF_WEEK_MONDAY,
    Alarm,
    AlarmAck,
    AlarmSnapshot,
    OneShot,
    Repeating,
)
from cozy.v1.common_pb2 import ERROR_CODE_UPSTREAM_UNAVAILABLE
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import (
    CANCEL_REASON_USER,
    STREAM_KIND_RESPONSE_DOWN,
    TextQuery,
    UtteranceCancel,
)
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.core.openai_gateway import OpenAIGateway
from app.main import create_app
from tests.agent_fixtures import NOW
from tests.conftest import (
    HUB_ID,
    connect_hub,
    connect_mobile,
    eventually,
    migrate,
    send,
)
from tests.fake_ha import FakeHomeAssistant
from tests.voice_support import (
    FakeOpenAI,
    State,
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
    states_of,
    text_turn,
    tool_turn,
)


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    fake.start()
    yield fake
    fake.close()


@pytest.fixture
def gateway() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def voice_settings(ha_settings: Settings) -> Settings:
    return ha_settings.model_copy(update={"max_utterance_seconds": 1.0})


def _build_client(settings: Settings, gateway: OpenAIGateway | None) -> Iterator[TestClient]:
    migrate(settings.database_url)
    with TestClient(create_app(settings, openai=gateway, clock=lambda: NOW)) as client:
        eventually(lambda: client.app.state.home.ready)  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def voice_client(voice_settings: Settings, gateway: FakeOpenAI) -> Iterator[TestClient]:
    yield from _build_client(voice_settings, gateway)


@pytest.fixture
def disabled_client(voice_settings: Settings) -> Iterator[TestClient]:
    yield from _build_client(voice_settings, None)


def _text_query(ws: WebSocketTestSession, text: str, request_id: str = "q1") -> list[Envelope]:
    send(ws, Envelope(text_query=TextQuery(request_id=request_id, text=text)))
    return recv_until(ws, is_final_text)


def test_matched_command_runs_full_voice_flow(
    voice_client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)

    assert states_of(frames) == [
        State.STATE_LISTENING,
        State.STATE_THINKING,
        State.STATE_SPEAKING,
        State.STATE_IDLE,
    ]
    transcript = of_body(frames, "transcript")[0].transcript
    assert (transcript.stream_id, transcript.text, transcript.final) == (
        "u1",
        "거실 조명 꺼줘",
        True,
    )
    calls = [c for c in fake_ha.service_calls if c["type"] == "call_service"]
    assert [(c["domain"], c["service"], c["target"]["entity_id"]) for c in calls] == [
        ("light", "turn_off", "light.living")
    ]
    assert spoken_text(frames) == "거실 조명을 껐어요"
    assert of_body(frames, "response_text")[-1].response_text.final
    assert gateway.tts_texts == ["거실 조명을 껐어요"]

    start = of_body(frames, "response_audio_start")[0].response_audio_start
    assert start.kind == STREAM_KIND_RESPONSE_DOWN
    assert (start.format.sample_rate_hz, start.format.channels) == (24000, 1)
    chunks = [f.audio_chunk for f in of_body(frames, "audio_chunk")]
    assert chunks and {c.kind for c in chunks} == {STREAM_KIND_RESPONSE_DOWN}
    assert {c.stream_id for c in chunks} == {start.stream_id}
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert [c.last for c in chunks] == [False] * (len(chunks) - 1) + [True]
    assert all(len(c.data) <= 1440 for c in chunks)
    ends = of_body(frames, "response_audio_end")
    assert ends[0].response_audio_end.stream_id == start.stream_id

    positions = {
        name: [i for i, f in enumerate(frames) if f.WhichOneof("body") == name]
        for name in ("response_audio_start", "audio_chunk", "response_audio_end")
    }
    assert positions["response_audio_start"][0] < positions["audio_chunk"][0]
    assert positions["audio_chunk"][-1] < positions["response_audio_end"][0]


def test_stt_prompt_contains_home_names(voice_client: TestClient, gateway: FakeOpenAI) -> None:
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    wav, prompt = gateway.stt_calls[0]
    assert wav[:4] == b"RIFF"
    assert prompt is not None
    assert "거실 조명" in prompt
    assert "침실" in prompt


def test_stt_prompt_can_be_disabled(voice_settings: Settings, gateway: FakeOpenAI) -> None:
    settings = voice_settings.model_copy(update={"stt_prompt_enabled": False})
    for client in _build_client(settings, gateway):
        with connect_hub(client) as hub:
            send_utterance(hub)
            recv_until(hub, is_idle)
    assert gateway.stt_calls[0][1] is None


def test_relative_brightness_is_clamped(
    voice_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    with connect_hub(voice_client) as hub:
        _text_query(hub, "거실 조명 밝게 해줘")
        _text_query(hub, "침실 조명 어둡게 해줘", "q2")

    calls = [c for c in fake_ha.service_calls if c["type"] == "call_service"]
    # 거실 200/255 = 78% + 30 -> 100 으로 잘린다. 침실 100/255 = 39% - 30 = 9.
    assert [c["service_data"]["brightness_pct"] for c in calls] == [100, 9]


def test_llm_fallback_runs_tool_then_streams_final_text(
    voice_client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    gateway.stt_text = "분위기 좀 바꿔줘"
    gateway.chat_turns = [
        tool_turn("control_device", {"entity_id": "light.living", "action": "turn_off"}),
        text_turn("분위기를 바꿨어요. ", "거실 조명을 껐어요"),
    ]
    with connect_hub(voice_client) as hub:
        send_utterance(hub, area_id="living_room")
        frames = recv_until(hub, is_idle)

    assert len(gateway.chat_calls) == 2
    first, second = gateway.chat_calls
    system = first["messages"][0]["content"]
    assert system.rstrip().endswith("현재 시각: 2026-11-13 15:30 금요일.")
    assert "허브가 있는 방: 거실" in system
    tool_names = [t["function"]["name"] for t in first["tools"]]
    assert tool_names == [
        "control_device",
        "get_device_state",
        "set_alarm",
        "set_timer",
        "cancel_alarm",
    ]
    assert (
        "light.living"
        in first["tools"][0]["function"]["parameters"]["properties"]["entity_id"]["enum"]
    )
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert second["messages"][3]["tool_call_id"] == "call-1"
    assert second["messages"][3]["content"] == '{"ok": true}'
    assert [c["service"] for c in fake_ha.service_calls if c["type"] == "call_service"] == [
        "turn_off"
    ]
    deltas = [
        f.response_text.delta for f in of_body(frames, "response_text") if f.response_text.delta
    ]
    assert deltas == ["분위기를 바꿨어요. ", "거실 조명을 껐어요"]
    assert gateway.tts_texts == ["분위기를 바꿨어요.", "거실 조명을 껐어요"]


def test_llm_failure_replies_with_apology_template(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    gateway.stt_text = "분위기 좀 바꿔줘"
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)
    assert spoken_text(frames) == "죄송해요, 지금은 답을 드리기 어려워요."


def test_clarify_follow_up_resolves_area_answer(
    voice_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    with connect_hub(voice_client) as hub:
        question = spoken_text(_text_query(hub, "불 꺼줘"))
        assert "어떤 건가요" in question
        answer = spoken_text(_text_query(hub, "거실", "q2"))

    assert answer == "거실 조명을 껐어요"
    calls = [c for c in fake_ha.service_calls if c["type"] == "call_service"]
    assert [c["target"]["entity_id"] for c in calls] == ["light.living"]


def test_clarify_no_cancels_and_expired_pending_is_ignored(
    voice_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    agent = voice_client.app.state.agent  # type: ignore[attr-defined]
    with connect_hub(voice_client) as hub:
        _text_query(hub, "불 꺼줘")
        assert spoken_text(_text_query(hub, "아니", "q2")) == "알겠어요, 취소할게요."

        _text_query(hub, "불 꺼줘", "q3")
        base = agent._monotonic()
        agent._monotonic = lambda: base + 31.0
        # 만료됐으므로 "거실" 은 새 발화로 본다. 매처가 못 풀어 LLM 이 없으면 사과 템플릿이 나간다.
        reply = spoken_text(_text_query(hub, "거실", "q4"))

    assert "껐어요" not in reply
    assert not [c for c in fake_ha.service_calls if c["type"] == "call_service"]


def test_alarm_intent_is_forwarded_to_hub_and_acked(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    gateway.stt_text = "내일 오전 7시에 알람 맞춰줘"
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)
        upsert = of_body(frames, "alarm_upsert")
        if not upsert:
            upsert = [recv_until(hub, lambda e: e.WhichOneof("body") == "alarm_upsert")[-1]]
        alarm = upsert[0].alarm_upsert.alarm
        request_id = upsert[0].alarm_upsert.request_id

        assert alarm.kind == ALARM_KIND_ALARM
        assert alarm.hub_id == HUB_ID
        assert alarm.alarm_id
        # 2026-11-14 07:00 KST
        assert alarm.one_shot.fires_at_epoch_ms == 1_794_607_200_000
        assert "알람을 맞췄어요" in spoken_text(frames)

        send(
            hub,
            Envelope(
                alarm_ack=AlarmAck(
                    request_id=request_id, alarm_id=alarm.alarm_id, revision=1, ok=True
                )
            ),
        )
        rows = eventually(
            lambda: [
                r
                for r in voice_client.get("/api/alarm", params={"hub_id": HUB_ID}).json()
                if r["sync_state"] == "synced"
            ]
        )
    assert rows[0]["alarm_id"] == alarm.alarm_id
    assert rows[0]["revision"] == 1


def test_timer_intent_creates_timer_on_hub(voice_client: TestClient, gateway: FakeOpenAI) -> None:
    gateway.stt_text = "10분 타이머 맞춰줘"
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)
        upsert = of_body(frames, "alarm_upsert") or [
            recv_until(hub, lambda e: e.WhichOneof("body") == "alarm_upsert")[-1]
        ]
    alarm = upsert[0].alarm_upsert.alarm
    assert alarm.kind == ALARM_KIND_TIMER
    assert alarm.one_shot.fires_at_epoch_ms == int(NOW.timestamp() * 1000) + 600_000


def test_cancel_alarm_intent_deletes_matching_alarm(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    gateway.stt_text = "7시 30분 알람 취소해줘"
    existing = Alarm(
        alarm_id="a1",
        kind=ALARM_KIND_ALARM,
        repeating=Repeating(hour=7, minute=30, days=[DAY_OF_WEEK_MONDAY]),
        enabled=True,
        revision=1,
        hub_id=HUB_ID,
    )
    with connect_hub(voice_client) as hub:
        send(hub, Envelope(alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID, alarms=[existing])))
        eventually(lambda: voice_client.get("/api/alarm", params={"hub_id": HUB_ID}).json())
        send_utterance(hub)
        frames = recv_until(hub, is_idle)
        deletes = of_body(frames, "alarm_delete") or [
            recv_until(hub, lambda e: e.WhichOneof("body") == "alarm_delete")[-1]
        ]
    assert deletes[0].alarm_delete.alarm_id == "a1"
    assert "취소했어요" in spoken_text(frames)


def test_cancel_mid_flight_stops_work_and_returns_to_idle(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    import asyncio

    gateway.stt_block = asyncio.Event()
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        thinking = recv_until(
            hub,
            lambda e: (
                e.WhichOneof("body") == "assistant_state"
                and e.assistant_state.state == State.STATE_THINKING
            ),
        )
        assert states_of(thinking) == [State.STATE_LISTENING, State.STATE_THINKING]
        send(
            hub,
            Envelope(utterance_cancel=UtteranceCancel(stream_id="u1", reason=CANCEL_REASON_USER)),
        )
        idle = recv_until(hub, is_idle)
        assert idle[-1].assistant_state.stream_id == "u1"
        eventually(lambda: gateway.stt_cancelled)

        from cozy.v1.session_pb2 import Ping

        send(hub, Envelope(ping=Ping(sent_epoch_ms=1)))
        after = recv_until(hub, lambda e: e.WhichOneof("body") == "pong")
    assert not of_body(after, "transcript")
    assert not of_body(after, "response_text")
    assert not gateway.tts_texts


def test_oversize_utterance_is_rejected(voice_client: TestClient, gateway: FakeOpenAI) -> None:
    with connect_hub(voice_client) as hub:
        send_utterance_start(hub, rate=16000)
        send_chunk(hub, 0, size=40000)
        frames = recv_until(hub, is_idle)
        error = of_body(frames, "error")[0].error
        send_chunk(hub, 1)
        send_end(hub)
        from cozy.v1.session_pb2 import Ping

        send(hub, Envelope(ping=Ping(sent_epoch_ms=1)))
        after = recv_until(hub, lambda e: e.WhichOneof("body") == "pong")

    assert error.message_key == "voice.utterance_too_long"
    assert error.stream_id == "u1"
    assert states_of(frames) == [State.STATE_LISTENING, State.STATE_IDLE]
    assert not of_body(after, "assistant_state")
    assert not gateway.stt_calls


def test_out_of_order_chunk_aborts_utterance(voice_client: TestClient, gateway: FakeOpenAI) -> None:
    with connect_hub(voice_client) as hub:
        send_utterance_start(hub)
        send_chunk(hub, 0)
        send_chunk(hub, 2)
        frames = recv_until(hub, is_idle)
    assert of_body(frames, "error")[0].error.message_key == "voice.stream_invalid"
    assert not gateway.stt_calls


def test_tts_failure_falls_back_to_subtitles_only(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    gateway.tts_fail = True
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)

    assert spoken_text(frames) == "거실 조명을 껐어요"
    assert of_body(frames, "response_text")[-1].response_text.final
    assert not of_body(frames, "response_audio_start")
    assert not of_body(frames, "audio_chunk")
    assert states_of(frames) == [State.STATE_LISTENING, State.STATE_THINKING, State.STATE_IDLE]


def test_stt_failure_sends_unavailable_subtitle(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    from app.core.openai_gateway import OpenAIGatewayError

    async def failing(wav: bytes, prompt: str | None = None) -> str:
        raise OpenAIGatewayError("down")

    gateway.transcribe = failing  # type: ignore[method-assign]
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)
    assert "음성 명령을 처리할 수 없어요" in spoken_text(frames)
    assert not gateway.tts_texts


def test_empty_transcript_asks_again(voice_client: TestClient, gateway: FakeOpenAI) -> None:
    gateway.stt_text = "  "
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, is_idle)
    assert spoken_text(frames) == "잘 못 들었어요. 다시 말씀해 주세요."
    assert not of_body(frames, "transcript")


def test_text_query_from_mobile_replies_with_text_only(
    voice_client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    with connect_mobile(voice_client) as mobile:
        frames = _text_query(mobile, "거실 조명 꺼줘", "q9")

    texts = of_body(frames, "response_text")
    assert {f.response_text.stream_id for f in texts} == {"q9"}
    assert spoken_text(frames) == "거실 조명을 껐어요"
    assert not of_body(frames, "audio_chunk")
    assert not gateway.tts_texts
    assert [c["service"] for c in fake_ha.service_calls if c["type"] == "call_service"] == [
        "turn_off"
    ]


def test_state_query_is_answered_from_cache(voice_client: TestClient) -> None:
    with connect_mobile(voice_client) as mobile:
        frames = _text_query(mobile, "거실 조명 켜져 있어?")
    assert spoken_text(frames) == "거실 조명이 켜져 있어요"


def test_voice_disabled_answers_with_upstream_unavailable(disabled_client: TestClient) -> None:
    with connect_hub(disabled_client) as hub:
        send(hub, Envelope(text_query=TextQuery(request_id="q1", text="거실 조명 꺼줘")))
        error = recv_until(hub, lambda e: e.WhichOneof("body") == "error")[-1].error
        send_utterance_start(hub, "u9")
        started = recv_until(hub, lambda e: e.WhichOneof("body") == "error")[-1].error

    assert (error.code, error.request_id) == (ERROR_CODE_UPSTREAM_UNAVAILABLE, "q1")
    assert started.code == ERROR_CODE_UPSTREAM_UNAVAILABLE
    assert started.stream_id == "u9"


def test_hub_disconnect_cancels_in_flight_work(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    import asyncio

    gateway.stt_block = asyncio.Event()
    with connect_hub(voice_client) as hub:
        send_utterance(hub)
        recv_until(
            hub,
            lambda e: (
                e.WhichOneof("body") == "assistant_state"
                and e.assistant_state.state == State.STATE_THINKING
            ),
        )
    eventually(lambda: gateway.stt_cancelled)


_TOMORROW_7_MS = int(NOW.replace(day=14, hour=7, minute=0).timestamp() * 1000)
_TIMER_5M_MS = int(NOW.timestamp() * 1000) + 300_000
_DAY_MS = 86_400_000


def _seed(client: TestClient, hub: WebSocketTestSession, alarms: list[Alarm]) -> None:
    send(hub, Envelope(alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID, alarms=alarms)))
    eventually(
        lambda: len(client.get("/api/alarm", params={"hub_id": HUB_ID}).json()) == len(alarms)
    )


def _one_shot(alarm_id: str, fires_ms: int, *, timer: bool = False, label: str = "") -> Alarm:
    return Alarm(
        alarm_id=alarm_id,
        kind=ALARM_KIND_TIMER if timer else ALARM_KIND_ALARM,
        label=label,
        one_shot=OneShot(fires_at_epoch_ms=fires_ms),
        enabled=True,
        revision=1,
        hub_id=HUB_ID,
    )


def _repeating(alarm_id: str, hour: int, label: str = "") -> Alarm:
    return Alarm(
        alarm_id=alarm_id,
        kind=ALARM_KIND_ALARM,
        label=label,
        repeating=Repeating(hour=hour, minute=0, days=[DAY_OF_WEEK_MONDAY]),
        enabled=True,
        revision=1,
        hub_id=HUB_ID,
    )


def _deleted_ids(hub: WebSocketTestSession, frames: list[Envelope]) -> list[str]:
    deletes = of_body(frames, "alarm_delete") or [
        recv_until(hub, lambda e: e.WhichOneof("body") == "alarm_delete")[-1]
    ]
    return [d.alarm_delete.alarm_id for d in deletes]


def test_timer_cancel_by_duration_skips_llm_and_deletes_timer(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    timers = [
        _one_shot("t5", _TIMER_5M_MS, timer=True),
        _one_shot("t10", _TIMER_5M_MS + 300_000, timer=True),
    ]
    with connect_hub(voice_client) as hub:
        _seed(voice_client, hub, timers)
        frames = _text_query(hub, "5분 타이머 취소해줘")
        assert _deleted_ids(hub, frames) == ["t5"]
    assert "5분 타이머를 취소했어요" in spoken_text(frames)
    assert gateway.chat_calls == []


def test_timer_cancel_uses_original_duration_after_time_passed(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    with connect_hub(voice_client) as hub:
        created = _text_query(hub, "5분 타이머 맞춰줘")
        upserts = of_body(created, "alarm_upsert") or [
            recv_until(hub, lambda e: e.WhichOneof("body") == "alarm_upsert")[-1]
        ]
        timer_id = upserts[0].alarm_upsert.alarm.alarm_id
        agent = voice_client.app.state.agent  # type: ignore[attr-defined]
        agent._clock = lambda: NOW + timedelta(minutes=3)
        frames = _text_query(hub, "5분짜리 타이머 꺼줘", "q2")
        assert _deleted_ids(hub, frames) == [timer_id]
    assert gateway.chat_calls == []


def test_alarm_cancel_by_day_and_time_skips_llm(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    alarms = [
        _one_shot("a-tomorrow", _TOMORROW_7_MS),
        _one_shot("a-today", _TOMORROW_7_MS - _DAY_MS),
    ]
    with connect_hub(voice_client) as hub:
        _seed(voice_client, hub, alarms)
        frames = _text_query(hub, "내일 아침 7시 알람 취소해줘")
        assert _deleted_ids(hub, frames) == ["a-tomorrow"]
    assert "내일 오전 7시 알람을 취소했어요" in spoken_text(frames)
    assert gateway.chat_calls == []


def test_cancel_without_match_answers_not_found(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    with connect_hub(voice_client) as hub:
        assert spoken_text(_text_query(hub, "5분 타이머 취소해줘")) == "해당 알람이 없어요."
    assert gateway.chat_calls == []


def test_ambiguous_cancel_lists_candidates_and_ordinal_resolves(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    with connect_hub(voice_client) as hub:
        _seed(voice_client, hub, [_repeating("am", 7, "운동"), _repeating("pm", 19)])
        question = spoken_text(_text_query(hub, "알람 취소해줘"))
        assert "첫 번째" in question and "두 번째" in question
        assert "어떤 걸 취소할까요" in question
        frames = _text_query(hub, "두 번째", "q2")
        assert _deleted_ids(hub, frames) == ["pm"]
    assert gateway.chat_calls == []


def test_ambiguous_cancel_follow_up_by_clock_word(
    voice_client: TestClient, gateway: FakeOpenAI
) -> None:
    with connect_hub(voice_client) as hub:
        _seed(voice_client, hub, [_repeating("am", 7), _repeating("pm", 19)])
        _text_query(hub, "알람 취소해줘")
        frames = _text_query(hub, "저녁 7시 꺼", "q2")
        assert _deleted_ids(hub, frames) == ["pm"]
    assert gateway.chat_calls == []
