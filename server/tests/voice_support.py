"""음성 파이프라인 테스트 공용 도구: 가짜 OpenAIGateway, 한국어 가짜 집, 프레임 수집기."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import (
    AUDIO_ENCODING_PCM_S16LE,
    AssistantState,
    AudioChunk,
    AudioFormat,
    UtteranceEnd,
    UtteranceStart,
)
from cozy.v1.voice_pb2 import STREAM_KIND_UTTERANCE_UP as UTTERANCE_UP
from starlette.testclient import WebSocketTestSession

from app.core.openai_gateway import ChatChunk, OpenAIGateway, OpenAIGatewayError, ToolCall
from tests.conftest import recv, send
from tests.fake_ha import FakeHomeAssistant, _state

State = AssistantState.State
TTS_PCM_BYTES = 24000 * 2 // 5  # 200ms


class FakeOpenAI(OpenAIGateway):
    """네트워크 없이 STT/LLM/TTS 를 흉내 내는 가짜. 호출 기록을 남긴다."""

    def __init__(self) -> None:  # 부모 __init__ 은 실제 HTTP 클라이언트를 만들므로 부르지 않는다
        self.stt_text = "거실 조명 꺼줘"
        self.stt_block: asyncio.Event | None = None
        self.stt_cancelled = False
        self.stt_calls: list[tuple[bytes, str | None]] = []
        self.chat_turns: list[list[ChatChunk]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.tts_texts: list[str] = []
        self.tts_fail = False

    async def aclose(self) -> None:
        return None

    async def transcribe(self, wav: bytes, prompt: str | None = None) -> str:
        self.stt_calls.append((wav, prompt))
        if self.stt_block is not None:
            try:
                await self.stt_block.wait()
            except asyncio.CancelledError:
                self.stt_cancelled = True
                raise
        return self.stt_text

    async def stream_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        self.chat_calls.append({"messages": json.loads(json.dumps(list(messages))), "tools": tools})
        if not self.chat_turns:
            raise OpenAIGatewayError("no scripted turn")
        for chunk in self.chat_turns.pop(0):
            yield chunk

    async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
        self.tts_texts.append(text)
        if self.tts_fail:
            raise OpenAIGatewayError("tts down")
        yield b"\x01\x00" * (TTS_PCM_BYTES // 2 // 2)
        yield b"\x02\x00" * (TTS_PCM_BYTES // 2 // 2)


def tool_turn(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> list[ChatChunk]:
    call = ToolCall(call_id, name, json.dumps(arguments))
    return [ChatChunk(tool_calls=(call,))]


def text_turn(*parts: str) -> list[ChatChunk]:
    return [ChatChunk(text=p) for p in parts]


def make_korean_home(fake: FakeHomeAssistant) -> None:
    """가짜 HA 의 집을 한국어 이름으로 바꾼다. start() 전에 불러야 한다."""
    fake.states = [
        _state("light.living", "on", {"friendly_name": "거실 조명", "brightness": 200}),
        _state("light.bed", "on", {"friendly_name": "침실 조명", "brightness": 100}),
        _state("sensor.temp", "21.5", {"friendly_name": "거실 온도", "unit_of_measurement": "C"}),
    ]
    fake.areas = [
        {"area_id": "living_room", "name": "거실"},
        {"area_id": "bedroom", "name": "침실"},
    ]
    fake.devices = []
    fake.entity_registry = [
        {"entity_id": "light.living", "area_id": "living_room", "device_id": None},
        {"entity_id": "light.bed", "area_id": "bedroom", "device_id": None},
        {"entity_id": "sensor.temp", "area_id": "living_room", "device_id": None},
    ]
    fake.exposed = {"light.living", "light.bed", "sensor.temp"}


def recv_until(
    ws: WebSocketTestSession,
    stop: Callable[[Envelope], bool],
    max_frames: int = 200,
) -> list[Envelope]:
    """stop 조건을 만족하는 프레임까지 읽어 모두 돌려준다. 도착 순서에 기대지 않는다."""
    frames: list[Envelope] = []
    for _ in range(max_frames):
        envelope = recv(ws)
        frames.append(envelope)
        if stop(envelope):
            return frames
    raise AssertionError(f"Stop condition not met in {max_frames} frames")


def is_idle(envelope: Envelope) -> bool:
    return (
        envelope.WhichOneof("body") == "assistant_state"
        and envelope.assistant_state.state == State.STATE_IDLE
    )


def is_final_text(envelope: Envelope) -> bool:
    return envelope.WhichOneof("body") == "response_text" and envelope.response_text.final


def of_body(frames: list[Envelope], body: str) -> list[Envelope]:
    return [f for f in frames if f.WhichOneof("body") == body]


def states_of(frames: list[Envelope]) -> list[int]:
    return [f.assistant_state.state for f in of_body(frames, "assistant_state")]


def spoken_text(frames: list[Envelope]) -> str:
    return "".join(f.response_text.delta for f in of_body(frames, "response_text"))


def send_utterance_start(
    ws: WebSocketTestSession, stream_id: str = "u1", rate: int = 16000, area_id: str = ""
) -> None:
    start = UtteranceStart(
        stream_id=stream_id,
        format=AudioFormat(encoding=AUDIO_ENCODING_PCM_S16LE, sample_rate_hz=rate, channels=1),
        area_id=area_id,
    )
    send(ws, Envelope(utterance_start=start))


def send_chunk(
    ws: WebSocketTestSession, index: int, size: int = 640, stream_id: str = "u1", last: bool = False
) -> None:
    chunk = AudioChunk(
        kind=UTTERANCE_UP,
        stream_id=stream_id,
        index=index,
        data=b"\x00\x01" * (size // 2),
        last=last,
    )
    send(ws, Envelope(audio_chunk=chunk))


def send_end(ws: WebSocketTestSession, stream_id: str = "u1") -> None:
    send(ws, Envelope(utterance_end=UtteranceEnd(stream_id=stream_id)))


def send_utterance(
    ws: WebSocketTestSession, stream_id: str = "u1", area_id: str = "", chunks: int = 3
) -> None:
    """발화 한 건(시작, 청크, 끝)을 보낸다."""
    send_utterance_start(ws, stream_id, area_id=area_id)
    for index in range(chunks):
        send_chunk(ws, index, stream_id=stream_id, last=index == chunks - 1)
    send_end(ws, stream_id)
