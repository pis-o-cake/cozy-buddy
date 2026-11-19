"""ResponseStreamer: 취소·예외로 끊겨도 종료 프레임을 보낸다."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from cozy.v1.envelope_pb2 import Envelope

from app.domain.voice.responder import ResponseStreamer
from tests.voice_support import FakeOpenAI


class _Sink:
    def __init__(self, fail_after: int | None = None) -> None:
        self.frames: list[Envelope] = []
        self._fail_after = fail_after

    async def __call__(self, envelope: Envelope) -> None:
        if self._fail_after is not None and len(self.frames) >= self._fail_after:
            raise ConnectionError("closed")
        self.frames.append(envelope)

    def bodies(self) -> list[str | None]:
        return [f.WhichOneof("body") for f in self.frames]

    def finals(self) -> int:
        return sum(
            1
            for f in self.frames
            if f.WhichOneof("body") == "response_text" and f.response_text.final
        )


class _Speech(FakeOpenAI):
    def __init__(self, started: asyncio.Event) -> None:
        super().__init__()
        self._started = started

    async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
        yield b"\x01\x00" * 5000
        self._started.set()
        await asyncio.sleep(10)
        yield b"\x01\x00"


async def _broken() -> AsyncIterator[str]:
    yield "일부"
    raise RuntimeError("boom")


async def test_cancel_during_audio_sends_audio_end_and_final_text() -> None:
    started = asyncio.Event()
    sink = _Sink()
    streamer = ResponseStreamer(sink, "s1", _Speech(started))

    async def deltas() -> AsyncIterator[str]:
        yield "안녕하세요. 반가워요. "
        await asyncio.sleep(10)

    task = asyncio.create_task(streamer.run(deltas()))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "response_audio_start" in sink.bodies()
    assert sink.bodies().count("response_audio_end") == 1
    assert sink.finals() == 1


async def test_exception_in_deltas_still_sends_final_text() -> None:
    sink = _Sink()
    with pytest.raises(RuntimeError, match="boom"):
        await ResponseStreamer(sink, "s1", None).run(_broken())

    assert sink.finals() == 1


async def test_terminal_send_errors_are_swallowed_when_connection_is_gone() -> None:
    sink = _Sink(fail_after=1)
    with pytest.raises(RuntimeError, match="boom"):
        await ResponseStreamer(sink, "s1", None).run(_broken())

    assert sink.finals() == 0
