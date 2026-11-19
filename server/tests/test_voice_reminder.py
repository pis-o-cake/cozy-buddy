"""리마인더 전체 흐름: 발화로 REMINDER 알람 생성, 허브의 ReminderFired 로 안내 음성 합성."""

from collections.abc import Iterator

import pytest
from cozy.v1.alarm_pb2 import ALARM_KIND_REMINDER, ReminderFired
from cozy.v1.common_pb2 import ERROR_CODE_BAD_REQUEST
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import STREAM_KIND_RESPONSE_DOWN
from fastapi.testclient import TestClient

from app.config import Settings
from tests.conftest import connect_hub, connect_mobile, send
from tests.fake_ha import FakeHomeAssistant
from tests.test_voice import _build_client
from tests.voice_support import (
    FakeOpenAI,
    make_korean_home,
    of_body,
    recv_until,
    send_utterance,
    spoken_text,
)


@pytest.fixture
def reminder_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    fake.start()
    yield fake
    fake.close()


@pytest.fixture
def tts() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def client(
    ha_settings: Settings, reminder_ha: FakeHomeAssistant, tts: FakeOpenAI
) -> Iterator[TestClient]:
    yield from _build_client(ha_settings.model_copy(update={"max_utterance_seconds": 1.0}), tts)


@pytest.fixture
def silent_client(ha_settings: Settings, reminder_ha: FakeHomeAssistant) -> Iterator[TestClient]:
    yield from _build_client(ha_settings.model_copy(update={"max_utterance_seconds": 1.0}), None)


def _fired(hub: object, alarm_id: str, label: str) -> list[Envelope]:
    send(hub, Envelope(reminder_fired=ReminderFired(alarm_id=alarm_id, label=label)))  # type: ignore[arg-type]
    return recv_until(hub, lambda e: e.WhichOneof("body") == "response_audio_end")  # type: ignore[arg-type]


def test_reminder_utterance_creates_reminder_alarm_on_hub(
    client: TestClient, tts: FakeOpenAI
) -> None:
    tts.stt_text = "내일 3시에 병원 가라고 알려줘"
    with connect_hub(client) as hub:
        send_utterance(hub)
        frames = recv_until(hub, lambda e: e.WhichOneof("body") == "alarm_upsert")

    alarm = of_body(frames, "alarm_upsert")[0].alarm_upsert.alarm
    assert alarm.kind == ALARM_KIND_REMINDER
    assert alarm.label == "병원 가기"
    # 2026-11-14 15:00 KST
    assert alarm.one_shot.fires_at_epoch_ms == 1_794_636_000_000


def test_reminder_fired_is_spoken_as_response_audio(client: TestClient, tts: FakeOpenAI) -> None:
    with connect_hub(client) as hub:
        frames = _fired(hub, "m1", "병원 가기")

    start = of_body(frames, "response_audio_start")[0].response_audio_start
    chunks = [f.audio_chunk for f in of_body(frames, "audio_chunk")]
    end = of_body(frames, "response_audio_end")[0].response_audio_end

    assert tts.tts_texts == ["병원 가실 시간이에요"]
    assert start.kind == STREAM_KIND_RESPONSE_DOWN
    assert start.stream_id == "reminder-m1-r"
    assert chunks and all(c.stream_id == start.stream_id for c in chunks)
    assert chunks[-1].last
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert end.stream_id == start.stream_id
    assert spoken_text(frames) == "병원 가실 시간이에요"
    assert not of_body(frames, "assistant_state")


def test_reminder_with_noun_label_is_spoken_with_template(
    client: TestClient, tts: FakeOpenAI
) -> None:
    with connect_hub(client) as hub:
        _fired(hub, "m2", "회의")

    assert tts.tts_texts == ["회의 시간이에요"]


def test_reminder_without_tts_gateway_sends_subtitle_only(silent_client: TestClient) -> None:
    with connect_hub(silent_client) as hub:
        send(hub, Envelope(reminder_fired=ReminderFired(alarm_id="m3", label="약 먹기")))
        frames = recv_until(
            hub, lambda e: e.WhichOneof("body") == "response_text" and e.response_text.final
        )

    assert spoken_text(frames) == "약 먹으실 시간이에요"
    assert not of_body(frames, "response_audio_start")


def test_reminder_fired_with_empty_label_is_rejected(client: TestClient) -> None:
    with connect_hub(client) as hub:
        send(hub, Envelope(reminder_fired=ReminderFired(alarm_id="m4", label="  ")))
        frames = recv_until(hub, lambda e: e.WhichOneof("body") == "error")

    error = frames[-1].error
    assert error.code == ERROR_CODE_BAD_REQUEST
    # 스트림 없는 오류가 빈 ID 로 나가면 허브가 진행 중인 발화의 실패로 읽는다.
    assert error.request_id == "m4" and not error.stream_id


def test_mobile_cannot_trigger_reminder_speech(client: TestClient) -> None:
    with connect_mobile(client) as mobile:
        send(mobile, Envelope(reminder_fired=ReminderFired(alarm_id="m5", label="약 먹기")))
        frames = recv_until(mobile, lambda e: e.WhichOneof("body") == "error")

    assert frames[-1].error.code == ERROR_CODE_BAD_REQUEST
