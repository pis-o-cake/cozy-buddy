"""STT 헤지·재시도·제한 시간: 가짜 전송 계층으로 게이트웨이 동작을 검증한다."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import openai
import pytest

from app.config import Settings
from app.core.openai_gateway import OpenAIGateway, OpenAIGatewayError, OpenAIGatewayTimeout

HEDGE_MS = 50
TIMEOUT_MS = 400

Behavior = Callable[[int], Awaitable[str]]


class Probe(OpenAIGateway):
    """SDK 호출 지점만 가짜로 바꾼 게이트웨이. 호출 번호별 동작과 취소 여부를 기록한다."""

    def __init__(self, behavior: Behavior) -> None:
        super().__init__(
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                openai_api_key="test",  # type: ignore[arg-type]
                openai_stt_model="stt",
                openai_llm_model="llm",
                openai_tts_model="tts",
                openai_tts_voice="voice",
                stt_hedge_after_ms=HEDGE_MS,
                stt_timeout_ms=TIMEOUT_MS,
            )
        )
        self.behavior = behavior
        self.calls = 0
        self.cancelled: list[int] = []

    async def _create_transcription(self, wav: bytes, prompt: str | None) -> str:
        self.calls += 1
        number = self.calls
        try:
            return await self.behavior(number)
        except asyncio.CancelledError:
            self.cancelled.append(number)
            raise


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")


def _server_error() -> openai.InternalServerError:
    return openai.InternalServerError(
        "boom", response=httpx.Response(503, request=_request()), body=None
    )


async def _sleep_then(seconds: float, text: str) -> str:
    await asyncio.sleep(seconds)
    return text


async def _transcribe(gateway: Probe) -> tuple[str, list[bool]]:
    hedges: list[bool] = []
    text = await gateway.transcribe(b"wav", on_hedge=lambda: hedges.append(True))
    return text, hedges


async def test_fast_first_request_does_not_hedge() -> None:
    gateway = Probe(lambda n: _sleep_then(0.0, "빠름"))
    text, hedges = await _transcribe(gateway)
    assert (text, hedges, gateway.calls) == ("빠름", [], 1)


async def test_slow_first_request_hedges_and_hedge_wins() -> None:
    async def behavior(n: int) -> str:
        return await _sleep_then(5.0 if n == 1 else 0.0, f"요청{n}")

    gateway = Probe(behavior)
    text, hedges = await _transcribe(gateway)
    assert (text, hedges, gateway.calls) == ("요청2", [True], 2)
    assert gateway.cancelled == [1]


async def test_first_request_can_still_win_after_hedge() -> None:
    async def behavior(n: int) -> str:
        return await _sleep_then(0.1 if n == 1 else 5.0, f"요청{n}")

    gateway = Probe(behavior)
    text, hedges = await _transcribe(gateway)
    assert (text, hedges) == ("요청1", [True])
    assert gateway.cancelled == [2]


async def test_both_slow_hits_deadline_and_cancels_both() -> None:
    gateway = Probe(lambda n: _sleep_then(5.0, "늦음"))
    with pytest.raises(OpenAIGatewayTimeout):
        await _transcribe(gateway)
    assert sorted(gateway.cancelled) == [1, 2]


async def test_5xx_is_retried_once_immediately() -> None:
    async def behavior(n: int) -> str:
        if n == 1:
            raise _server_error()
        return "재시도 성공"

    gateway = Probe(behavior)
    text, hedges = await _transcribe(gateway)
    assert (text, hedges, gateway.calls) == ("재시도 성공", [], 2)


async def test_connection_error_is_retried_then_fails() -> None:
    async def behavior(n: int) -> str:
        raise openai.APIConnectionError(request=_request())

    gateway = Probe(behavior)
    with pytest.raises(OpenAIGatewayError) as info:
        await _transcribe(gateway)
    assert not isinstance(info.value, OpenAIGatewayTimeout)
    assert gateway.calls == 2


async def test_non_retryable_error_is_not_retried() -> None:
    async def behavior(n: int) -> str:
        raise openai.AuthenticationError(
            "bad key", response=httpx.Response(401, request=_request()), body=None
        )

    gateway = Probe(behavior)
    with pytest.raises(OpenAIGatewayError):
        await _transcribe(gateway)
    assert gateway.calls == 1


async def test_hedge_disabled_waits_for_single_request() -> None:
    gateway = Probe(lambda n: _sleep_then(0.15, "느린 단일"))
    gateway._stt_hedge_after = 0
    text, hedges = await _transcribe(gateway)
    assert (text, hedges, gateway.calls) == ("느린 단일", [], 1)
