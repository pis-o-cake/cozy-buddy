"""턴 기록: 음성·텍스트 흐름에서 경로, 의도, 단계 시간이 채워진다."""

from collections.abc import Callable, Iterator

import pytest
from cozy.v1.alarm_pb2 import ALARM_CONTROL_ACTION_STOP, AlarmControlResult
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import HubStatus
from cozy.v1.voice_pb2 import TextQuery
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.core.openai_gateway import OpenAIGatewayError, OpenAIGatewayTimeout
from app.domain.telemetry.recorder import TurnRecorder
from app.domain.telemetry.turn import SkillOutcome, Turn, TurnPath, TurnSource
from app.main import create_app
from tests.agent_fixtures import NOW
from tests.conftest import connect_hub, connect_mobile, eventually, migrate, send
from tests.fake_ha import FakeHomeAssistant
from tests.voice_support import (
    FakeOpenAI,
    is_final_text,
    is_idle,
    make_korean_home,
    recv_until,
    send_utterance,
    text_turn,
    tool_turn,
)


class Captured:
    """recorder.finish 로 끝난 턴을 모은다. 기록기는 그대로 동작한다."""

    def __init__(self, recorder: TurnRecorder) -> None:
        self.turns: list[Turn] = []
        original = recorder.finish

        def capture(turn: Turn) -> None:
            self.turns.append(turn)
            original(turn)

        recorder.finish = capture  # type: ignore[method-assign]

    def one(self) -> Turn:
        """정확히 한 건이 끝날 때까지 기다려 돌려준다."""
        eventually(lambda: len(self.turns) >= 1)
        assert len(self.turns) == 1
        return self.turns[0]


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
def turn_client(ha_settings: Settings, gateway: FakeOpenAI) -> Iterator[TestClient]:
    migrate(ha_settings.database_url)
    with TestClient(create_app(ha_settings, openai=gateway, clock=lambda: NOW)) as client:
        eventually(lambda: client.app.state.home.ready)  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def captured(turn_client: TestClient) -> Captured:
    return Captured(turn_client.app.state.turns)  # type: ignore[attr-defined]


def _text(ws: WebSocketTestSession, text: str) -> None:
    send(ws, Envelope(text_query=TextQuery(request_id="q1", text=text)))
    recv_until(ws, is_final_text)


def _non_negative(*values: int | None) -> bool:
    return all(v is not None and v >= 0 for v in values)


def test_voice_turn_records_matcher_path_and_all_stage_times(
    turn_client: TestClient, captured: Captured
) -> None:
    with connect_hub(turn_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    turn = captured.one()
    assert turn.source is TurnSource.VOICE and turn.hub_id == "hub-1"
    assert turn.transcript == "거실 조명 꺼줘"
    assert (turn.path, turn.intent) == (TurnPath.MATCHER, "DeviceCommand")
    assert turn.skill_result is SkillOutcome.OK and turn.llm_tool_calls == 0
    assert turn.response_text == "거실 조명을 껐어요" and turn.error_code == ""
    assert _non_negative(turn.t_stt_ms, turn.t_understand_ms, turn.t_act_ms, turn.t_first_audio_ms)


def test_text_turn_has_no_stt_or_audio_time(turn_client: TestClient, captured: Captured) -> None:
    with connect_mobile(turn_client) as mobile:
        _text(mobile, "거실 조명 켜져 있어?")

    turn = captured.one()
    assert turn.source is TurnSource.TEXT
    assert (turn.path, turn.intent) == (TurnPath.MATCHER, "StateQuery")
    assert turn.t_stt_ms is None and turn.t_first_audio_ms is None
    assert _non_negative(turn.t_understand_ms, turn.t_act_ms)


def test_llm_turn_counts_tool_calls_and_times_both_stages(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    gateway.chat_turns = [
        tool_turn("control_device", {"entity_id": "light.living", "action": "turn_off"}),
        text_turn("껐어요."),
    ]
    with connect_mobile(turn_client) as mobile:
        _text(mobile, "불 좀 어떻게 해봐")

    turn = captured.one()
    assert turn.path is TurnPath.LLM and turn.llm_tool_calls == 1
    assert turn.skill_result is SkillOutcome.OK and turn.response_text == "껐어요."
    assert _non_negative(turn.t_understand_ms, turn.t_act_ms)


def test_llm_failure_is_logged_on_the_turn(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    with connect_mobile(turn_client) as mobile:
        _text(mobile, "농담 하나 해줘")

    turn = captured.one()
    assert (turn.path, turn.error_code) == (TurnPath.LLM, "llm_error")
    assert turn.response_text


def test_clarify_question_is_clarify_path_and_answer_is_context(
    turn_client: TestClient, captured: Captured
) -> None:
    with connect_mobile(turn_client) as mobile:
        _text(mobile, "불 꺼줘")
        _text(mobile, "침실")

    eventually(lambda: len(captured.turns) == 2)
    asked, answered = captured.turns
    assert asked.path is TurnPath.CLARIFY and asked.intent == "Clarify"
    assert answered.path is TurnPath.CONTEXT and answered.skill_result is SkillOutcome.OK


def test_failed_device_command_marks_skill_failed(
    turn_client: TestClient, fake_ha: FakeHomeAssistant, captured: Captured
) -> None:
    fake_ha.service_error = "boom"
    with connect_mobile(turn_client) as mobile:
        _text(mobile, "거실 조명 꺼줘")

    turn = captured.one()
    assert turn.path is TurnPath.MATCHER and turn.skill_result is SkillOutcome.FAILED


def test_empty_transcript_is_clarify_path(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    gateway.stt_text = "  "
    with connect_hub(turn_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    turn = captured.one()
    assert turn.path is TurnPath.CLARIFY and turn.t_stt_ms is not None
    assert turn.transcript == "" and turn.response_text


def test_stt_failure_records_error_turn(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    async def broken(wav: bytes, prompt: str | None = None, **_: object) -> str:
        raise OpenAIGatewayError("stt down")

    gateway.transcribe = broken  # type: ignore[method-assign]
    with connect_hub(turn_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    turn = captured.one()
    assert (turn.path, turn.error_code) == (TurnPath.ERROR, "stt_failed")


def test_stt_timeout_records_error_turn_with_timeout_code(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    async def slow(wav: bytes, prompt: str | None = None, **_: object) -> str:
        raise OpenAIGatewayTimeout("STT deadline exceeded")

    gateway.transcribe = slow  # type: ignore[method-assign]
    with connect_hub(turn_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    turn = captured.one()
    assert (turn.path, turn.error_code) == (TurnPath.ERROR, "stt_timeout")


def test_hedged_stt_is_flagged_on_turn(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    async def hedged(
        wav: bytes, prompt: str | None = None, on_hedge: Callable[[], None] | None = None
    ) -> str:
        assert on_hedge is not None
        on_hedge()
        return "거실 조명 꺼줘"

    gateway.transcribe = hedged  # type: ignore[method-assign]
    with connect_hub(turn_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    assert captured.one().stt_hedged is True


def test_unhedged_stt_is_not_flagged(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    with connect_hub(turn_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    assert captured.one().stt_hedged is False


def _alarm_control_of(frame: Envelope) -> bool:
    return frame.WhichOneof("body") == "alarm_control"


def test_ringing_hub_stop_command_goes_to_hub_then_replies(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    gateway.stt_text = "알람 꺼줘"
    hub_control = turn_client.app.state.connections.hub_control  # type: ignore[attr-defined]
    with connect_hub(turn_client) as hub:
        send(hub, Envelope(hub_status=HubStatus(ringing_alarm_id="a1")))
        eventually(lambda: hub_control.ringing_alarm_id("hub-1"))
        send_utterance(hub)
        control = recv_until(hub, _alarm_control_of)[-1].alarm_control
        assert (control.alarm_id, control.action) == ("a1", ALARM_CONTROL_ACTION_STOP)
        result = AlarmControlResult(request_id=control.request_id, ok=True)
        send(hub, Envelope(alarm_control_result=result))
        frames = recv_until(hub, is_idle)

    assert any(f.WhichOneof("body") == "response_audio_start" for f in frames)
    turn = captured.one()
    assert (turn.path, turn.intent) == (TurnPath.MATCHER, "AlarmControlIntent")
    assert turn.skill_result is SkillOutcome.OK and turn.response_text == "알람을 껐어요"


def test_not_ringing_hub_keeps_cancel_alarm_meaning(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    gateway.stt_text = "알람 꺼줘"
    with connect_hub(turn_client) as hub:
        send_utterance(hub)
        recv_until(hub, is_idle)

    assert captured.one().intent == "CancelAlarmIntent"


def test_unexpected_tool_error_yields_internal_fallback(
    turn_client: TestClient, gateway: FakeOpenAI, captured: Captured
) -> None:
    async def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("tool crashed")

    turn_client.app.state.agent._skills.run_tool = explode  # type: ignore[attr-defined]
    gateway.chat_turns = [
        tool_turn("control_device", {"entity_id": "light.living", "action": "turn_off"}),
    ]
    with connect_mobile(turn_client) as mobile:
        _text(mobile, "불 좀 어떻게 해봐")

    turn = captured.one()
    assert (turn.path, turn.error_code) == (TurnPath.ERROR, "internal")
    assert turn.response_text == turn_client.app.state.translator("error.internal")  # type: ignore[attr-defined]
