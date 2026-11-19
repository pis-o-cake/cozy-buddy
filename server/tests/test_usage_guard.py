"""하루 사용량 예산, 토큰 버킷, 게이트웨이 거절 규칙."""

import io
import wave
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from loguru import logger
from pydantic import SecretStr

from app.config import Settings
from app.core.openai_gateway import (
    OpenAIGateway,
    UsageLimitExceeded,
    charge_llm,
    charge_stt,
    charge_tts,
)
from app.core.usage_guard import BucketRegistry, TokenBucket, UsageGuard, UsageKind

TZ = ZoneInfo("Asia/Seoul")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_settings(**update: int) -> Settings:
    return Settings(_env_file=None, **update)  # type: ignore[call-arg]


def wav_of(seconds: float, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def test_consume_refuses_when_over_limit_and_does_not_add() -> None:
    guard = UsageGuard(make_settings(daily_llm_calls=2))
    assert guard.consume(UsageKind.LLM_CALLS, 1)
    assert guard.consume(UsageKind.LLM_CALLS, 1)
    assert not guard.consume(UsageKind.LLM_CALLS, 1)
    assert guard.snapshot().used[UsageKind.LLM_CALLS] == 2


def test_zero_limit_means_unlimited() -> None:
    guard = UsageGuard(make_settings(daily_tts_chars=0))
    assert guard.consume(UsageKind.TTS_CHARS, 10_000_000)


def test_counters_reset_at_local_midnight() -> None:
    clock = Clock(datetime(2026, 11, 19, 23, 59, tzinfo=TZ))
    guard = UsageGuard(make_settings(daily_llm_calls=1), clock)
    assert guard.consume(UsageKind.LLM_CALLS, 1)
    assert not guard.consume(UsageKind.LLM_CALLS, 1)
    clock.now += timedelta(minutes=2)
    assert guard.consume(UsageKind.LLM_CALLS, 1)
    assert guard.snapshot().day == clock.now.date()


def test_error_is_logged_once_per_day_per_kind() -> None:
    messages: list[str] = []
    sink = logger.add(lambda m: messages.append(str(m)), level="ERROR", format="{message}")
    try:
        clock = Clock(datetime(2026, 11, 19, 10, 0, tzinfo=TZ))
        guard = UsageGuard(make_settings(daily_llm_calls=1), clock)
        guard.consume(UsageKind.LLM_CALLS, 1)
        for _ in range(5):
            guard.consume(UsageKind.LLM_CALLS, 1)
        assert len([m for m in messages if "Daily budget exceeded" in m]) == 1
        clock.now += timedelta(days=1)
        guard.consume(UsageKind.LLM_CALLS, 1)
        guard.consume(UsageKind.LLM_CALLS, 1)
        assert len([m for m in messages if "Daily budget exceeded" in m]) == 2
    finally:
        logger.remove(sink)


def test_charge_helpers_raise_when_exhausted() -> None:
    guard = UsageGuard(make_settings(daily_stt_seconds=1, daily_llm_calls=1, daily_tts_chars=5))
    charge_stt(guard, wav_of(0.6))
    with pytest.raises(UsageLimitExceeded):
        charge_stt(guard, wav_of(0.6))
    charge_llm(guard)
    with pytest.raises(UsageLimitExceeded):
        charge_llm(guard)
    charge_tts(guard, "12345")
    with pytest.raises(UsageLimitExceeded):
        charge_tts(guard, "1")


class _Untouched:
    """한도에 걸린 요청은 OpenAI 클라이언트에 닿지 않아야 한다."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"OpenAI client touched: {name}")


@pytest.mark.asyncio
async def test_real_gateway_refuses_before_calling_openai() -> None:
    settings = make_settings(
        daily_stt_seconds=1,
        daily_llm_calls=1,
        daily_tts_chars=1,
        openai_api_key=SecretStr("sk-test"),
        openai_stt_model="s",
        openai_llm_model="l",
        openai_tts_model="t",
        openai_tts_voice="v",
    )
    gateway = OpenAIGateway(settings)
    gateway.usage.consume(UsageKind.STT_SECONDS, 1)
    gateway.usage.consume(UsageKind.LLM_CALLS, 1)
    gateway.usage.consume(UsageKind.TTS_CHARS, 1)
    await gateway._client.close()
    gateway._client = _Untouched()  # type: ignore[assignment]

    with pytest.raises(UsageLimitExceeded):
        await gateway.transcribe(wav_of(0.5))
    with pytest.raises(UsageLimitExceeded):
        _ = [c async for c in gateway.stream_chat([{"role": "user", "content": "x"}])]
    with pytest.raises(UsageLimitExceeded):
        _ = [c async for c in gateway.stream_speech("안녕")]


def test_token_bucket_allows_burst_then_refills() -> None:
    now = [0.0]
    bucket = TokenBucket(20, lambda: now[0])
    assert [bucket.try_take() for _ in range(20)] == [True] * 20
    assert not bucket.try_take()
    now[0] += 3.0  # 20/min = 1 token per 3 s
    assert bucket.try_take()
    assert not bucket.try_take()


def test_bucket_registry_isolates_clients() -> None:
    registry = BucketRegistry(1, lambda: 0.0)
    assert registry.try_take("a")
    assert not registry.try_take("a")
    assert registry.try_take("b")
