"""TTS 캐시: 저장소, 응답 스트리머 적중·미스, 웜업, 턴 기록."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from pathlib import Path

import pytest
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import Entity, HomeSnapshot
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.i18n import Translator
from app.core.openai_gateway import OpenAIGatewayError
from app.domain.voice.responder import ResponseStreamer
from app.domain.voice.tts_cache import TtsCache, TtsCacheLimits
from app.domain.voice.tts_warmup import TtsWarmup
from app.main import create_app
from tests.agent_fixtures import NOW
from tests.conftest import connect_hub, eventually, migrate
from tests.fake_ha import FakeHomeAssistant
from tests.voice_support import (
    FakeOpenAI,
    is_idle,
    make_korean_home,
    recv_until,
    send_utterance,
    text_turn,
)

MB = 1024 * 1024
SENTENCE = "거실 조명을 껐어요"


def make_cache(
    path: Path | None, memory: int = MB, disk: int = 10 * MB, identity: str = "m|v"
) -> TtsCache:
    return TtsCache(path, identity, TtsCacheLimits(memory, disk))


def files(path: Path, pattern: str) -> list[Path]:
    return list(path.glob(pattern))


def disk_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.glob("*.pcm"))


async def deltas(*parts: str) -> AsyncIterator[str]:
    for part in parts:
        yield part


class Sink:
    def __init__(self) -> None:
        self.frames: list[Envelope] = []

    async def __call__(self, envelope: Envelope) -> None:
        self.frames.append(envelope)

    def audio(self) -> list[bytes]:
        return [f.audio_chunk.data for f in self.frames if f.WhichOneof("body") == "audio_chunk"]

    def kinds(self) -> list[str | None]:
        return [
            f.WhichOneof("body") for f in self.frames if f.WhichOneof("body") != "response_text"
        ]


async def speak(
    gateway: FakeOpenAI, cache: TtsCache | None, text: str, cacheable: bool = True
) -> tuple[Sink, ResponseStreamer]:
    sink = Sink()
    streamer = ResponseStreamer(sink, "s1", gateway, cache=cache, cacheable=lambda: cacheable)
    await streamer.run(deltas(text))
    return sink, streamer


# --- 저장소 ----------------------------------------------------------------------------------


async def test_disk_persists_across_instances(tmp_path: Path) -> None:
    first = make_cache(tmp_path)
    first.put("문장", b"\x01\x02" * 100)
    await first.drain()

    second = make_cache(tmp_path)

    assert second.has("문장")
    assert await second.get("문장") == b"\x01\x02" * 100
    assert not files(tmp_path, "*.tmp")


async def test_identity_change_misses(tmp_path: Path) -> None:
    first = make_cache(tmp_path, identity="model-a|voice")
    first.put("문장", b"\x00\x00")
    await first.drain()

    assert make_cache(tmp_path, identity="model-b|voice").has("문장") is False


async def test_memory_is_bounded_and_evicts_lru() -> None:
    cache = make_cache(None, memory=1000)
    cache.put("a", b"\x00" * 400)
    cache.put("b", b"\x00" * 400)
    assert await cache.get("a") is not None
    cache.put("c", b"\x00" * 400)

    assert cache.memory_bytes <= 1000
    assert await cache.get("a") is not None
    assert await cache.get("c") is not None
    assert await cache.get("b") is None


async def test_disk_is_bounded_and_evicts_oldest(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, memory=100, disk=1000)
    for name in ("a", "b", "c"):
        cache.put(name, b"\x00" * 400)
        await cache.drain()

    assert cache.disk_bytes <= 1000
    assert not cache.has("a") and cache.has("b") and cache.has("c")
    assert disk_size(tmp_path) <= 1000


async def test_startup_trims_oversized_directory(tmp_path: Path) -> None:
    big = make_cache(tmp_path, disk=10 * MB)
    for name in ("a", "b", "c"):
        big.put(name, b"\x00" * 400)
        await big.drain()

    small = make_cache(tmp_path, disk=800)

    assert small.disk_bytes <= 800
    assert len(files(tmp_path, "*.pcm")) == small.disk_entries


async def test_oversized_entry_is_skipped() -> None:
    cache = TtsCache(None, "x", TtsCacheLimits(MB, 0, max_entry_bytes=100))
    cache.put("a", b"\x00" * 101)

    assert not cache.has("a")


# --- 스트리머 --------------------------------------------------------------------------------


async def test_miss_then_hit_sends_identical_frames_without_tts_call() -> None:
    gateway, cache = FakeOpenAI(), make_cache(None)

    miss, first = await speak(gateway, cache, SENTENCE)
    hit, second = await speak(gateway, cache, SENTENCE)

    assert gateway.tts_texts == [SENTENCE]
    assert (first.first_sentence_cache_hit, second.first_sentence_cache_hit) == (False, True)
    assert hit.kinds() == miss.kinds()
    assert hit.audio() == miss.audio()
    assert (cache.hits, cache.misses) == (1, 1)


async def test_incomplete_stream_is_not_stored() -> None:
    class Broken(FakeOpenAI):
        async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
            yield b"\x01\x00" * 100
            raise OpenAIGatewayError("cut")

    cache = make_cache(None)

    await speak(Broken(), cache, SENTENCE)

    assert not cache.has(SENTENCE)


async def test_cancelled_stream_is_not_stored() -> None:
    started = asyncio.Event()

    class Slow(FakeOpenAI):
        async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
            yield b"\x01\x00" * 100
            started.set()
            await asyncio.sleep(10)
            yield b"\x01\x00"

    cache = make_cache(None)
    task = asyncio.create_task(speak(Slow(), cache, SENTENCE))
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert not cache.has(SENTENCE)


async def test_non_cacheable_text_is_never_cached() -> None:
    gateway, cache = FakeOpenAI(), make_cache(None)

    await speak(gateway, cache, SENTENCE, cacheable=False)
    await speak(gateway, cache, SENTENCE, cacheable=False)

    assert gateway.tts_texts == [SENTENCE, SENTENCE]
    assert not cache.has(SENTENCE)


async def test_each_sentence_is_cached_separately() -> None:
    gateway, cache = FakeOpenAI(), make_cache(None)
    await speak(gateway, cache, "첫째예요. 둘째예요.")
    gateway.tts_texts.clear()

    await speak(gateway, cache, "둘째예요.")

    assert gateway.tts_texts == []


# --- 웜업 ------------------------------------------------------------------------------------


def snapshot(*entities: tuple[str, str, str]) -> HomeSnapshot:
    return HomeSnapshot(entities=[Entity(entity_id=i, name=n, domain=d) for i, n, d in entities])


def make_warmup(
    gateway: FakeOpenAI,
    cache: TtsCache,
    snap: Callable[[], HomeSnapshot],
    max_per_day: int = 100,
    today: Callable[[], date] | None = None,
) -> TtsWarmup:
    settings = Settings(_env_file=None, tts_warmup_max_per_day=max_per_day)  # type: ignore[call-arg]
    return TtsWarmup(settings, cache, gateway, Translator("ko"), snap, today)


async def test_warmup_fills_cache_and_skips_cached() -> None:
    gateway, cache = FakeOpenAI(), make_cache(None)
    snap = snapshot(("light.a", "거실 조명", "light"), ("sensor.t", "온도", "sensor"))
    warmup = make_warmup(gateway, cache, lambda: snap)

    first = await warmup.run_once()
    second = await warmup.run_once()

    assert first == len(warmup.sentences()) and second == 0
    assert "알람을 껐어요" in gateway.tts_texts
    assert all(cache.has(s) for s in warmup.sentences())
    assert not any("온도" in t for t in gateway.tts_texts)


async def test_warmup_respects_daily_budget_and_resets_next_day() -> None:
    gateway, cache = FakeOpenAI(), make_cache(None)
    snap = snapshot(("light.a", "거실 조명", "light"), ("light.b", "침실 조명", "light"))
    day = [date(2026, 11, 19)]
    warmup = make_warmup(gateway, cache, lambda: snap, max_per_day=3, today=lambda: day[0])

    assert await warmup.run_once() == 3
    assert await warmup.run_once() == 0
    assert len(gateway.tts_texts) == 3
    day[0] = date(2026, 11, 20)
    assert await warmup.run_once() == 3


async def test_warmup_waits_for_active_turn() -> None:
    gateway, cache = FakeOpenAI(), make_cache(None)
    warmup = make_warmup(gateway, cache, lambda: snapshot())

    with cache.turn_active():
        task = asyncio.create_task(warmup.run_once())
        await asyncio.sleep(0.5)
        assert gateway.tts_texts == []
        sink, _ = await speak(gateway, cache, SENTENCE)
        assert sink.audio()
        assert gateway.tts_texts == [SENTENCE]
    assert await task > 0


async def test_warmup_stop_cancels_pending_synthesis() -> None:
    gate = asyncio.Event()

    class Blocked(FakeOpenAI):
        async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
            await gate.wait()
            yield b"\x00\x00"

    cache = make_cache(None)
    warmup = make_warmup(Blocked(), cache, lambda: snapshot())
    warmup.start()
    await asyncio.sleep(0.1)
    await asyncio.wait_for(warmup.stop(), 1)

    assert not any(cache.has(s) for s in warmup.sentences())


# --- 통합: 턴 기록 ---------------------------------------------------------------------------


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    fake.start()
    yield fake
    fake.close()


@pytest.fixture
def cached_client(
    ha_settings: Settings, tmp_path: Path, fake_ha: FakeHomeAssistant
) -> Iterator[tuple[TestClient, FakeOpenAI]]:
    gateway = FakeOpenAI()
    settings = ha_settings.model_copy(
        update={"tts_cache_enabled": True, "tts_cache_dir": str(tmp_path / "cache")}
    )
    migrate(settings.database_url)
    with TestClient(create_app(settings, openai=gateway, clock=lambda: NOW)) as client:
        eventually(lambda: client.app.state.home.ready)  # type: ignore[attr-defined]
        yield client, gateway


def run_turn(client: TestClient, stream_id: str) -> None:
    with connect_hub(client) as hub:
        send_utterance(hub, stream_id)
        recv_until(hub, is_idle)


def test_second_identical_command_hits_cache_and_is_recorded(
    cached_client: tuple[TestClient, FakeOpenAI],
) -> None:
    client, gateway = cached_client

    run_turn(client, "u1")
    run_turn(client, "u2")

    assert gateway.tts_texts.count(SENTENCE) == 1
    eventually(lambda: client.get("/api/system/turns/summary").json()["total"] == 2)
    summary = client.get("/api/system/turns/summary").json()
    assert (summary["tts_cache_hit_count"], summary["tts_cache_hit_ratio"]) == (1, 0.5)
    hits = [t["tts_cache_hit"] for t in reversed(client.get("/api/system/turns").json())]
    assert hits == [False, True]


def test_llm_text_is_not_cached_by_default(
    cached_client: tuple[TestClient, FakeOpenAI],
) -> None:
    client, gateway = cached_client
    gateway.stt_text = "오늘 뭐 하면 좋을까"
    gateway.chat_turns = [text_turn("산책이 좋아요"), text_turn("산책이 좋아요")]

    run_turn(client, "u1")
    run_turn(client, "u2")

    assert gateway.tts_texts.count("산책이 좋아요") == 2


async def test_turn_active_restores_idle_on_cancel() -> None:
    cache = make_cache(None)

    async def turn() -> None:
        with cache.turn_active():
            await asyncio.sleep(10)

    task = asyncio.create_task(turn())
    await asyncio.sleep(0)
    assert cache.busy
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not cache.busy
    await asyncio.wait_for(cache.wait_idle(), timeout=1)


def test_turn_active_restores_idle_on_exception() -> None:
    cache = make_cache(None)
    with pytest.raises(RuntimeError), cache.turn_active():
        raise RuntimeError("boom")
    assert not cache.busy
    assert cache._idle.is_set()
