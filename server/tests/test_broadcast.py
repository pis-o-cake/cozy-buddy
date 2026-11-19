"""허브 간 방송: 가짜 허브 여러 대에 알림과 음성이 같은 모양으로 내려가는지 검증한다."""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator

import pytest
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import STREAM_KIND_BROADCAST_DOWN, TextQuery
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.core.connections import ConnectionManager
from app.domain.agent.model import AreaRef
from app.domain.broadcast.service import BroadcastRequest, BroadcastService
from app.main import create_app
from tests.conftest import (
    HUB_ID,
    MOBILE_TOKEN,
    connect_hub,
    migrate,
    recv,
    send,
)
from tests.voice_support import (
    FakeOpenAI,
    is_final_text,
    of_body,
    recv_until,
    spoken_text,
    tool_turn,
)

HUB_B = "hub-2"
HUB_B_TOKEN = "hub2-secret"
HUB_C = "hub-3"
HUB_C_TOKEN = "hub3-secret"
SPOKEN = "방송이에요. 밥 먹으세요"


@pytest.fixture
def gateway() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def three_hub_settings(settings: Settings) -> Settings:
    tokens = settings.static_tokens.get_secret_value() + f",hub:{HUB_C}:{HUB_C_TOKEN}"
    return settings.model_copy(update={"static_tokens": SecretStr(tokens)})


@pytest.fixture
def client(three_hub_settings: Settings, gateway: FakeOpenAI) -> Iterator[TestClient]:
    migrate(three_hub_settings.database_url)
    with TestClient(create_app(three_hub_settings, openai=gateway)) as c:
        yield c


def set_area(client: TestClient, hub_id: str, area_id: str) -> None:
    response = client.patch(f"/api/hub/{hub_id}", json={"area_id": area_id})
    assert response.status_code == 200


def say(ws: WebSocketTestSession, text: str) -> str:
    """텍스트 질의를 보내고 최종 자막까지 받은 응답 문장을 돌려준다."""
    send(ws, Envelope(text_query=TextQuery(request_id="q1", text=text)))
    return spoken_text(recv_until(ws, is_final_text))


def receive_broadcast(ws: WebSocketTestSession) -> list[Envelope]:
    return recv_until(ws, lambda e: e.WhichOneof("body") == "response_audio_end")


def assert_complete_broadcast(frames: list[Envelope], text: str, from_hub: str) -> str:
    """알림, 시작, 연속 순번 조각, 끝이 순서대로 오고 같은 스트림을 가리키는지 확인한다."""
    bodies = [f.WhichOneof("body") for f in frames]
    assert bodies[0] == "broadcast_notice"
    assert bodies[1] == "response_audio_start"
    assert bodies[-1] == "response_audio_end"
    assert set(bodies[2:-1]) == {"audio_chunk"}
    notice = frames[0].broadcast_notice
    assert (notice.from_hub_id, notice.text) == (from_hub, text)
    start = frames[1].response_audio_start
    assert start.kind == STREAM_KIND_BROADCAST_DOWN
    assert (start.format.sample_rate_hz, start.format.channels) == (24000, 1)
    chunks = [f.audio_chunk for f in frames[2:-1]]
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert [c.last for c in chunks] == [False] * (len(chunks) - 1) + [True]
    assert {c.kind for c in chunks} == {STREAM_KIND_BROADCAST_DOWN}
    stream_ids = {notice.stream_id, start.stream_id, frames[-1].response_audio_end.stream_id}
    stream_ids |= {c.stream_id for c in chunks}
    assert len(stream_ids) == 1
    return notice.stream_id


def test_area_broadcast_reaches_only_that_area(client: TestClient, gateway: FakeOpenAI) -> None:
    with (
        connect_hub(client) as speaker,
        connect_hub(client, HUB_B, HUB_B_TOKEN) as kitchen,
        connect_hub(client, HUB_C, HUB_C_TOKEN) as bedroom,
    ):
        set_area(client, HUB_B, "주방")
        set_area(client, HUB_C, "침실")

        first_reply = say(speaker, "주방에 밥 먹으라고 방송해줘")
        kitchen_frames = receive_broadcast(kitchen)
        second_reply = say(speaker, "침실에 이제 자라고 방송해줘")
        bedroom_frames = receive_broadcast(bedroom)

    assert (first_reply, second_reply) == ("주방에 방송했어요.", "침실에 방송했어요.")
    assert_complete_broadcast(kitchen_frames, "밥 먹으세요", HUB_ID)
    # 침실 허브가 처음 받은 방송이 두 번째 것이면 첫 방송은 침실로 가지 않았다.
    assert_complete_broadcast(bedroom_frames, "이제 자세요", HUB_ID)
    assert gateway.tts_texts == [SPOKEN, "방송이에요. 이제 자세요"]


def test_all_broadcast_fans_out_one_synthesis_to_every_other_hub(
    client: TestClient, gateway: FakeOpenAI
) -> None:
    with (
        connect_hub(client) as speaker,
        connect_hub(client, HUB_B, HUB_B_TOKEN) as first,
        connect_hub(client, HUB_C, HUB_C_TOKEN) as second,
    ):
        reply = say(speaker, "다들 밥 먹으라고 방송해줘")
        frames_first = receive_broadcast(first)
        frames_second = receive_broadcast(second)

    assert reply == "허브 2대에 방송했어요."
    assert_complete_broadcast(frames_first, "다들 밥 먹으세요", HUB_ID)
    assert_complete_broadcast(frames_second, "다들 밥 먹으세요", HUB_ID)
    pcm_first = b"".join(f.audio_chunk.data for f in of_body(frames_first, "audio_chunk"))
    pcm_second = b"".join(f.audio_chunk.data for f in of_body(frames_second, "audio_chunk"))
    assert pcm_first == pcm_second
    assert len(pcm_first) > 0
    assert gateway.tts_texts == ["방송이에요. 다들 밥 먹으세요"]


def test_speaker_is_never_a_target_even_in_the_named_area(client: TestClient) -> None:
    with connect_hub(client) as speaker, connect_hub(client, HUB_B, HUB_B_TOKEN):
        set_area(client, HUB_ID, "주방")

        reply = say(speaker, "주방에 밥 먹으라고 방송해줘")

    assert reply == "주방에는 방송할 허브가 없어요."


def test_offline_target_is_noted_in_the_confirmation(client: TestClient) -> None:
    with connect_hub(client, HUB_C, HUB_C_TOKEN):
        set_area(client, HUB_C, "주방")
    with connect_hub(client) as speaker, connect_hub(client, HUB_B, HUB_B_TOKEN) as online:
        set_area(client, HUB_B, "주방")

        reply = say(speaker, "주방에 밥 먹으라고 방송해줘")
        frames = receive_broadcast(online)

    assert reply == "주방에 방송했어요. 오프라인인 허브 1대는 제외했어요."
    assert_complete_broadcast(frames, "밥 먹으세요", HUB_ID)


def test_all_targets_offline_reports_failure_without_audio(
    client: TestClient, gateway: FakeOpenAI
) -> None:
    with connect_hub(client, HUB_B, HUB_B_TOKEN):
        set_area(client, HUB_B, "주방")
    with connect_hub(client) as speaker:
        reply = say(speaker, "주방에 밥 먹으라고 방송해줘")

    assert reply == "주방의 허브가 모두 오프라인이라 방송하지 못했어요."
    assert gateway.tts_texts == []


def test_everyone_offline_uses_the_generic_wording(client: TestClient) -> None:
    with connect_hub(client, HUB_B, HUB_B_TOKEN):
        pass
    with connect_hub(client) as speaker:
        reply = say(speaker, "다들 밥 먹으라고 방송해줘")

    assert reply == "다른 허브가 모두 오프라인이라 방송하지 못했어요."


def test_unknown_target_is_reported(client: TestClient) -> None:
    with connect_hub(client) as speaker, connect_hub(client, HUB_B, HUB_B_TOKEN):
        set_area(client, HUB_B, "주방")

        reply = say(speaker, "베란다에 밥 먹으라고 방송해줘")

    assert reply == "베란다에는 방송할 허브가 없어요."


def test_tts_failure_falls_back_to_notice_only(client: TestClient, gateway: FakeOpenAI) -> None:
    gateway.tts_fail = True
    with connect_hub(client) as speaker, connect_hub(client, HUB_B, HUB_B_TOKEN) as kitchen:
        set_area(client, HUB_B, "주방")

        reply = say(speaker, "주방에 밥 먹으라고 방송해줘")
        notice = recv(kitchen)

    assert notice.WhichOneof("body") == "broadcast_notice"
    # 확인은 자막 전달 기준이라 음성이 나중에 실패해도 그대로다.
    assert reply == "주방에 방송했어요."


def test_llm_tool_call_broadcasts(client: TestClient, gateway: FakeOpenAI) -> None:
    gateway.chat_turns = [
        tool_turn("broadcast", {"target": "주방", "message": "밥 먹을 시간이에요"}),
        [],
    ]
    with connect_hub(client) as speaker, connect_hub(client, HUB_B, HUB_B_TOKEN) as kitchen:
        set_area(client, HUB_B, "주방")

        say(speaker, "주방 사람들한테 밥때라고 알려 줘")
        frames = receive_broadcast(kitchen)

    assert_complete_broadcast(frames, "밥 먹을 시간이에요", HUB_ID)
    tool_names = [t["function"]["name"] for t in gateway.chat_calls[0]["tools"]]
    assert "broadcast" in tool_names


def test_service_resolves_area_names_and_aliases_to_hub_areas(client: TestClient) -> None:
    service: BroadcastService = client.app.state.broadcast  # type: ignore[attr-defined]
    areas = [AreaRef("kitchen", "주방", ("부엌",)), AreaRef("living", "거실")]
    with connect_hub(client) as _, connect_hub(client, HUB_B, HUB_B_TOKEN) as kitchen:
        set_area(client, HUB_B, "kitchen")

        report = client.portal.call(  # type: ignore[union-attr]
            service.send, BroadcastRequest("부엌", "밥 먹으세요", HUB_ID, areas=areas)
        )
        frames = receive_broadcast(kitchen)

    assert report.delivered == (HUB_B,)
    assert report.offline == ()
    assert_complete_broadcast(frames, "밥 먹으세요", HUB_ID)


def test_service_without_tts_sends_notice_only(client: TestClient) -> None:
    connections: ConnectionManager = client.app.state.connections  # type: ignore[attr-defined]
    silent = BroadcastService(
        client.app.state.translator,  # type: ignore[attr-defined]
        connections,
        lambda: client.app.state.db,  # type: ignore[attr-defined]
        None,
    )
    with connect_hub(client), connect_hub(client, HUB_B, HUB_B_TOKEN) as other:
        report = client.portal.call(  # type: ignore[union-attr]
            silent.send, BroadcastRequest(None, "밥 먹으세요", HUB_ID)
        )
        notice = recv(other)

    assert report.delivered == (HUB_B,)
    assert report.audio is False
    assert notice.WhichOneof("body") == "broadcast_notice"


def test_rest_requires_a_token(client: TestClient) -> None:
    del client.headers["authorization"]
    assert client.post("/api/broadcast", json={"text": "밥"}).status_code == 401
    wrong = client.post(
        "/api/broadcast", json={"text": "밥"}, headers={"Authorization": "Bearer nope"}
    )
    assert wrong.status_code == 401


def test_rest_broadcast_delivers_with_mobile_token(client: TestClient, gateway: FakeOpenAI) -> None:
    headers = {"Authorization": f"Bearer {MOBILE_TOKEN}"}
    with connect_hub(client) as _, connect_hub(client, HUB_B, HUB_B_TOKEN) as kitchen:
        set_area(client, HUB_B, "주방")

        response = client.post(
            "/api/broadcast", json={"target": "주방", "text": "밥 먹으세요"}, headers=headers
        )
        frames = receive_broadcast(kitchen)

    body = response.json()
    assert response.status_code == 200
    assert (body["delivered"], body["offline"], body["audio"]) == ([HUB_B], [], True)
    assert_complete_broadcast(frames, "밥 먹으세요", "")
    assert gateway.tts_texts == [SPOKEN]


def test_rest_unknown_target_is_not_found(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {MOBILE_TOKEN}"}
    with connect_hub(client):
        response = client.post(
            "/api/broadcast", json={"target": "베란다", "text": "밥"}, headers=headers
        )

    assert response.status_code == 404


def test_confirmation_is_sent_before_the_slow_broadcast_stream_ends(
    client: TestClient, gateway: FakeOpenAI
) -> None:
    release = threading.Event()
    original = gateway.stream_speech

    async def slow_speech(text: str) -> AsyncIterator[bytes]:
        async for pcm in original(text):
            await asyncio.get_running_loop().run_in_executor(None, release.wait, 5)
            yield pcm

    gateway.stream_speech = slow_speech  # type: ignore[method-assign]
    with connect_hub(client) as speaker, connect_hub(client, HUB_B, HUB_B_TOKEN) as kitchen:
        set_area(client, HUB_B, "주방")

        reply = say(speaker, "주방에 밥 먹으라고 방송해줘")
        notice = recv(kitchen)
        # 합성이 아직 막혀 있는 동안 확인과 자막이 이미 도착했다.
        assert not release.is_set()
        release.set()
        frames = [notice, *receive_broadcast(kitchen)]

    assert reply == "주방에 방송했어요."
    assert_complete_broadcast(frames, "밥 먹으세요", HUB_ID)


def test_rest_broadcast_without_hub_labels_the_sender(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {MOBILE_TOKEN}"}
    with connect_hub(client, HUB_B, HUB_B_TOKEN) as kitchen:
        client.post("/api/broadcast", json={"text": "밥 먹으세요"}, headers=headers)
        notice = recv(kitchen)

    assert notice.broadcast_notice.from_area == "휴대폰"
