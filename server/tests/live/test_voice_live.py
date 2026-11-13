"""실제 OpenAI 와 데모 Home Assistant 를 쓰는 종단 간 검증. 비용이 들어 기본 실행에서 제외한다.

실행: `poetry run pytest -m live -s tests/live`
키와 HA 접속 정보는 저장소 루트 .env(OPENAI_*, COZY_HA_*)에서 읽고, 출력하지 않는다.
발화 한 번의 비용은 1센트 미만이다.
"""

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import Entity, EntityCommand, HomeSnapshot
from cozy.v1.session_pb2 import CLIENT_KIND_HUB, ClientHello
from cozy.v1.voice_pb2 import (
    AUDIO_ENCODING_PCM_S16LE,
    STREAM_KIND_RESPONSE_DOWN,
    STREAM_KIND_UTTERANCE_UP,
    AssistantState,
    AudioChunk,
    AudioFormat,
    UtteranceEnd,
    UtteranceStart,
)
from websockets.asyncio.client import ClientConnection, connect

from app.config import Settings
from app.core.openai_gateway import TTS_SAMPLE_RATE_HZ, OpenAIGateway
from tests.conftest import migrate

pytestmark = pytest.mark.live

SERVER_DIR = Path(__file__).resolve().parents[2]
HUB_ID = "live-hub"
HUB_TOKEN = secrets.token_hex(16)  # 실행마다 새로 만드는 일회용 값
CHUNK_BYTES = TTS_SAMPLE_RATE_HZ * 2 * 20 // 1000
TAIL_SILENCE_MS = 300
FIND_TIMEOUT_SECONDS = 30.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    settings = Settings()
    if not settings.voice_enabled or not settings.ha_enabled:
        pytest.skip("OPENAI_* and COZY_HA_* must be set in the repository root .env")
    return settings


@pytest.fixture
def server_url(tmp_path: Path, live_settings: Settings) -> Iterator[str]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'live.db'}"
    migrate(db_url)
    port = _free_port()
    env = {
        "COZY_DATABASE_URL": db_url,
        "COZY_STATIC_TOKENS": f"hub:{HUB_ID}:{HUB_TOKEN}",
        "COZY_LOG_LEVEL": "INFO",
        "PYTHONPATH": f"{SERVER_DIR / 'gen'}{os.pathsep}{SERVER_DIR}",
    }
    log_path = tmp_path / "server.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],  # fmt: skip
            cwd=SERVER_DIR,
            env={**os.environ, **env},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.2)
            else:
                pytest.fail("Server did not start: " + log_path.read_text(errors="replace")[-400:])
            yield f"ws://127.0.0.1:{port}/ws"
        finally:
            process.terminate()
            process.wait(timeout=10)
    print("--- server log (INFO lines) ---")
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "Utterance done" in line or "WARNING" in line or "ERROR" in line:
            print(line[-200:])


async def _recv(ws: ClientConnection, limit: float = 20.0) -> Envelope:
    envelope = Envelope()
    async with asyncio.timeout(limit):
        envelope.ParseFromString(await ws.recv(decode=False))
    return envelope


async def _send(ws: ClientConnection, envelope: Envelope) -> None:
    await ws.send(envelope.SerializeToString())


async def _find_light(ws: ClientConnection) -> tuple[Entity, str]:
    """HomeSnapshot 에서 방이 있는 조명을 찾는다. HA 연결이 끝나 스냅샷이 차면 온다."""
    deadline = time.monotonic() + FIND_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        envelope = await _recv(ws)
        if envelope.WhichOneof("body") != "home_snapshot":
            continue
        snapshot: HomeSnapshot = envelope.home_snapshot
        area_names = {a.area_id: a.name for a in snapshot.areas}
        lights = [e for e in snapshot.entities if e.domain == "light" and e.area_id in area_names]
        if lights:
            light = next((e for e in lights if "거실" in area_names[e.area_id]), lights[0])
            return light, area_names[light.area_id]
    pytest.fail("No light with an area in the home snapshot")


async def _synthesize(gateway: OpenAIGateway, text: str) -> bytes:
    pcm = b"".join([chunk async for chunk in gateway.stream_speech(text)])
    return pcm + b"\x00" * (TTS_SAMPLE_RATE_HZ * 2 * TAIL_SILENCE_MS // 1000)


async def _chunks(pcm: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(pcm), CHUNK_BYTES):
        yield pcm[offset : offset + CHUNK_BYTES]


async def _restore(ws: ClientConnection, light: Entity) -> None:
    action = "turn_on" if light.state.state == "on" else "turn_off"
    await _send(
        ws,
        Envelope(
            entity_command=EntityCommand(
                request_id="restore", entity_id=light.entity_id, action=action
            )
        ),
    )
    if action == "turn_on" and light.state.attributes.get("brightness", "").isdecimal():
        pct = max(1, round(int(light.state.attributes["brightness"]) * 100 / 255))
        await _send(
            ws,
            Envelope(
                entity_command=EntityCommand(
                    request_id="restore-b",
                    entity_id=light.entity_id,
                    action="set_brightness",
                    params={"brightness_pct": str(pct)},
                )
            ),
        )
    await asyncio.sleep(1.0)


async def test_live_turn_off_light(server_url: str, live_settings: Settings) -> None:
    gateway = OpenAIGateway(live_settings)
    async with connect(server_url) as ws:
        hello = ClientHello(
            kind=CLIENT_KIND_HUB, client_id=HUB_ID, token=HUB_TOKEN, protocol_version=1
        )
        await _send(ws, Envelope(client_hello=hello))
        light, area_name = await _find_light(ws)
        # 방 이름과 "불" 은 매처가 해석하는 어휘다. 기기 이름이 영어여도 결정론 경로를 탄다.
        phrase = f"{area_name} 불 꺼줘"
        if light.state.state != "on":
            await _send(
                ws,
                Envelope(
                    entity_command=EntityCommand(
                        request_id="prep", entity_id=light.entity_id, action="turn_on"
                    )
                ),
            )
            await asyncio.sleep(1.5)
        pcm = await _synthesize(gateway, phrase)

        report: dict[str, object] = {"phrase": phrase, "entity": light.entity_id}
        try:
            fmt = AudioFormat(
                encoding=AUDIO_ENCODING_PCM_S16LE, sample_rate_hz=TTS_SAMPLE_RATE_HZ, channels=1
            )
            await _send(
                ws,
                Envelope(
                    utterance_start=UtteranceStart(
                        stream_id="live-1", format=fmt, area_id=light.area_id
                    )
                ),
            )
            index = 0
            async for data in _chunks(pcm):
                chunk = AudioChunk(
                    kind=STREAM_KIND_UTTERANCE_UP, stream_id="live-1", index=index, data=data
                )
                await _send(ws, Envelope(audio_chunk=chunk))
                index += 1
            t_end = time.monotonic()
            await _send(ws, Envelope(utterance_end=UtteranceEnd(stream_id="live-1")))

            states: list[int] = []
            text = ""
            first_audio: float | None = None
            audio_bytes = 0
            turned_off = False
            while True:
                envelope = await _recv(ws, limit=40.0)
                body = envelope.WhichOneof("body")
                if body == "transcript":
                    report["transcript"] = envelope.transcript.text
                elif body == "response_text":
                    text += envelope.response_text.delta
                elif (
                    body == "audio_chunk" and envelope.audio_chunk.kind == STREAM_KIND_RESPONSE_DOWN
                ):
                    first_audio = first_audio or time.monotonic()
                    audio_bytes += len(envelope.audio_chunk.data)
                elif body == "entity_state_changed":
                    changed = envelope.entity_state_changed
                    turned_off = turned_off or (
                        changed.entity_id == light.entity_id and changed.state.state == "off"
                    )
                elif body == "assistant_state":
                    states.append(envelope.assistant_state.state)
                    if envelope.assistant_state.state == AssistantState.STATE_IDLE:
                        break
                elif body == "error":
                    report["error"] = envelope.error.message_key
                    break
            report.update(
                response_text=text,
                states=states,
                end_to_first_audio_ms=None
                if first_audio is None
                else int((first_audio - t_end) * 1000),
                audio_seconds=round(audio_bytes / 2 / TTS_SAMPLE_RATE_HZ, 2),
                ha_light_turned_off=turned_off,
            )
        finally:
            await _restore(ws, light)
            await gateway.aclose()
    print("LIVE REPORT:", report)
    assert "error" not in report
    assert report["ha_light_turned_off"] or "껐" in str(report["response_text"])
