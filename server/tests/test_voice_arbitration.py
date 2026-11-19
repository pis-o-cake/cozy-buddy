"""다중 허브 중재: 가짜 허브 두 대가 같은 발화를 보내는 WebSocket 흐름."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from cozy.v1.voice_pb2 import DECLINE_REASON_DUPLICATE
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.telemetry.recorder import TurnRecorder
from app.domain.telemetry.turn import Turn, TurnPath
from app.main import create_app
from tests.agent_fixtures import NOW
from tests.conftest import connect_hub, eventually, migrate
from tests.fake_ha import FakeHomeAssistant
from tests.voice_support import (
    FakeOpenAI,
    is_idle,
    make_korean_home,
    of_body,
    recv_until,
    send_utterance,
    spoken_text,
)

HUB_2 = ("hub-2", "hub2-secret")


class SequencedOpenAI(FakeOpenAI):
    """STT 호출 순서대로 다른 전사문을 돌려준다."""

    def __init__(self, texts: list[str]) -> None:
        super().__init__()
        self._texts = texts

    async def transcribe(
        self, wav: bytes, prompt: str | None = None, on_hedge: object = None
    ) -> str:
        self.stt_calls.append((wav, prompt))
        return self._texts[len(self.stt_calls) - 1]


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    fake.start()
    yield fake
    fake.close()


@contextmanager
def _client(settings: Settings, gateway: FakeOpenAI) -> Iterator[TestClient]:
    migrate(settings.database_url)
    with TestClient(create_app(settings, openai=gateway, clock=lambda: NOW)) as client:
        eventually(lambda: client.app.state.home.ready)  # type: ignore[attr-defined]
        yield client


def _turns(client: TestClient) -> list[Turn]:
    finished: list[Turn] = []
    recorder: TurnRecorder = client.app.state.turns  # type: ignore[attr-defined]
    original = recorder.finish

    def capture(turn: Turn) -> None:
        finished.append(turn)
        original(turn)

    recorder.finish = capture  # type: ignore[method-assign]
    return finished


def test_same_utterance_from_two_hubs_is_answered_once(
    ha_settings: Settings, fake_ha: FakeHomeAssistant
) -> None:
    gateway = FakeOpenAI()
    with _client(ha_settings, gateway) as client:
        turns = _turns(client)
        with connect_hub(client) as hub1, connect_hub(client, *HUB_2) as hub2:
            send_utterance(hub1, "a1")
            send_utterance(hub2, "b1")
            frames1 = recv_until(hub1, is_idle)
            frames2 = recv_until(hub2, is_idle)
        eventually(lambda: len(turns) == 2)

    assert len(gateway.stt_calls) == 2
    assert spoken_text(frames1) == "거실 조명을 껐어요"
    assert of_body(frames1, "utterance_declined") == []
    declined = of_body(frames2, "utterance_declined")
    assert len(declined) == 1
    message = declined[0].utterance_declined
    assert (message.stream_id, message.reason, message.winner_hub_id) == (
        "b1",
        DECLINE_REASON_DUPLICATE,
        "hub-1",
    )
    assert not of_body(frames2, "response_text")
    assert not of_body(frames2, "response_audio_start")
    assert not of_body(frames2, "transcript")
    assert gateway.tts_texts == ["거실 조명을 껐어요"]
    calls = [c for c in fake_ha.service_calls if c["type"] == "call_service"]
    assert len(calls) == 1
    assert sorted(t.path.value for t in turns if t.path) == ["declined", "matcher"]


def test_different_sentences_from_two_hubs_are_both_answered(ha_settings: Settings) -> None:
    gateway = SequencedOpenAI(["거실 조명 꺼줘", "침실 조명 꺼줘"])
    with (
        _client(ha_settings, gateway) as client,
        connect_hub(client) as hub1,
        connect_hub(client, *HUB_2) as hub2,
    ):
        send_utterance(hub1, "a1")
        send_utterance(hub2, "b1")
        frames1 = recv_until(hub1, is_idle)
        frames2 = recv_until(hub2, is_idle)

    assert of_body(frames1, "utterance_declined") == []
    assert of_body(frames2, "utterance_declined") == []
    assert {spoken_text(frames1), spoken_text(frames2)} == {
        "거실 조명을 껐어요",
        "침실 조명을 껐어요",
    }


def test_declined_path_value() -> None:
    assert TurnPath("declined") is TurnPath.DECLINED
