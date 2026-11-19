"""턴 로그: 저장, 집계 계산, 보존 기간 정리, REST 응답, 구조화 로그."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from app.config import Settings
from app.core.database import Database, utcnow
from app.domain.telemetry.models import TurnLog
from app.domain.telemetry.summary import percentile, summarize
from app.domain.telemetry.turn import Turn, TurnPath, TurnSource
from app.main import create_app
from tests.agent_fixtures import NOW
from tests.conftest import connect_mobile, eventually, migrate
from tests.fake_ha import FakeHomeAssistant
from tests.test_turn_flow import _text
from tests.voice_support import FakeOpenAI, make_korean_home


def make_row(
    turn_id: str,
    path: TurnPath = TurnPath.MATCHER,
    *,
    hub_id: str = "hub-1",
    age: timedelta = timedelta(0),
    stt: int | None = None,
    understand: int | None = None,
    act: int | None = None,
    first_audio: int | None = None,
    hedged: bool = False,
    cache_hit: bool = False,
) -> TurnLog:
    return TurnLog(
        turn_id=turn_id,
        hub_id=hub_id,
        source="voice",
        transcript="거실 불 꺼줘",
        path=path.value,
        intent="DeviceCommand",
        skill_result="ok",
        response_text="껐어요",
        t_stt_ms=stt,
        t_understand_ms=understand,
        t_act_ms=act,
        t_first_audio_ms=first_audio,
        llm_tool_calls=0,
        stt_hedged=hedged,
        tts_cache_hit=cache_hit,
        error_code="",
        created_at=utcnow() - age,
    )


def seed(url: str, rows: list[TurnLog]) -> None:
    async def run() -> None:
        db = Database(url)
        async with db.session() as session:
            session.add_all(rows)
            await session.commit()
        await db.dispose()

    asyncio.run(run())


def count_rows(url: str) -> list[str]:
    from sqlalchemy import select

    async def run() -> list[str]:
        db = Database(url)
        async with db.session() as session:
            ids = list((await session.scalars(select(TurnLog.turn_id))).all())
        await db.dispose()
        return sorted(ids)

    return asyncio.run(run())


# --- 집계 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "p50", "p95"),
    [
        ([], None, None),
        ([7], 7, 7),
        ([4, 1, 3, 2], 2, 4),
        (list(range(1, 101)), 50, 95),
        (list(range(1, 21)), 10, 19),
    ],
)
def test_percentile_uses_nearest_rank(values: list[int], p50: int | None, p95: int | None) -> None:
    assert percentile(values, 50) == p50
    assert percentile(values, 95) == p95


def test_summary_path_ratios_and_stage_percentiles() -> None:
    rows = [
        make_row("a", TurnPath.MATCHER, stt=600, understand=1, act=30, first_audio=1400),
        make_row("b", TurnPath.MATCHER, stt=700, understand=2, act=40, first_audio=1500),
        make_row("c", TurnPath.CONTEXT, stt=650, understand=3, act=50, first_audio=1600),
        make_row("d", TurnPath.LLM, stt=800, understand=900, act=200, first_audio=2500),
        make_row("e", TurnPath.ERROR, stt=None, understand=None, act=None, first_audio=None),
    ]

    summary = summarize(rows, 24)

    assert summary.total == 5
    assert {k: (v.count, v.ratio) for k, v in summary.paths.items()} == {
        "context": (1, 0.2),
        "matcher": (2, 0.4),
        "weather": (0, 0.0),
        "llm": (1, 0.2),
        "clarify": (0, 0.0),
        "error": (1, 0.2),
        "declined": (0, 0.0),
    }
    assert summary.deterministic_ratio == 0.6
    stt = summary.stages["t_stt_ms"]
    assert (stt.count, stt.p50_ms, stt.p95_ms) == (4, 650, 800)
    first_audio = summary.stages["t_first_audio_ms"]
    assert (first_audio.count, first_audio.p50_ms, first_audio.p95_ms) == (4, 1500, 2500)


def test_summary_reports_stt_hedge_rate_over_stt_turns() -> None:
    rows = [
        make_row("a", stt=600),
        make_row("b", stt=2500, hedged=True),
        make_row("c", stt=700),
        make_row("d", stt=800),
        make_row("text", stt=None),
    ]

    summary = summarize(rows, 24)

    assert summary.stt_hedged_count == 1
    assert summary.stt_hedge_ratio == 0.25


def test_hedge_flag_is_served_by_api(client: TestClient, settings: Settings) -> None:
    seed(settings.database_url, [make_row("h", stt=2500, hedged=True), make_row("n", stt=600)])

    items = client.get("/api/system/turns").json()
    summary = client.get("/api/system/turns/summary").json()

    assert {row["turn_id"]: row["stt_hedged"] for row in items} == {"h": True, "n": False}
    assert (summary["stt_hedged_count"], summary["stt_hedge_ratio"]) == (1, 0.5)


def test_empty_summary_is_zeroed() -> None:
    summary = summarize([], 24)

    assert summary.total == 0 and summary.deterministic_ratio == 0.0
    assert summary.stages["t_act_ms"].p50_ms is None
    assert all(p.count == 0 and p.ratio == 0.0 for p in summary.paths.values())


# --- REST ----------------------------------------------------------------------------------


def test_list_turns_newest_first_with_hub_filter_and_limit(
    client: TestClient, settings: Settings
) -> None:
    seed(
        settings.database_url,
        [
            make_row("old", age=timedelta(hours=3)),
            make_row("mid", age=timedelta(hours=2), hub_id="hub-2"),
            make_row("new", age=timedelta(hours=1)),
        ],
    )

    everything = client.get("/api/system/turns").json()
    only_hub1 = client.get("/api/system/turns", params={"hub_id": "hub-1"}).json()
    limited = client.get("/api/system/turns", params={"limit": 1}).json()

    assert [t["turn_id"] for t in everything] == ["new", "mid", "old"]
    assert [t["turn_id"] for t in only_hub1] == ["new", "old"]
    assert [t["turn_id"] for t in limited] == ["new"]
    assert everything[0]["path"] == "matcher" and everything[0]["transcript"] == "거실 불 꺼줘"


@pytest.mark.parametrize("limit", [0, 501])
def test_list_turns_rejects_out_of_range_limit(client: TestClient, limit: int) -> None:
    assert client.get("/api/system/turns", params={"limit": limit}).status_code == 422


def test_summary_endpoint_respects_window_and_hub(client: TestClient, settings: Settings) -> None:
    seed(
        settings.database_url,
        [
            make_row("in1", TurnPath.MATCHER, age=timedelta(hours=1), stt=500),
            make_row("in2", TurnPath.LLM, age=timedelta(hours=5), stt=900, hub_id="hub-2"),
            make_row("out", TurnPath.LLM, age=timedelta(hours=30), stt=9000),
        ],
    )

    default = client.get("/api/system/turns/summary").json()
    narrow = client.get("/api/system/turns/summary", params={"window_hours": 2}).json()
    wide = client.get("/api/system/turns/summary", params={"window_hours": 48}).json()
    hub2 = client.get("/api/system/turns/summary", params={"hub_id": "hub-2"}).json()

    assert (default["total"], default["window_hours"]) == (2, 24.0)
    assert narrow["total"] == 1 and narrow["paths"]["matcher"] == {"count": 1, "ratio": 1.0}
    assert wide["total"] == 3 and wide["stages"]["t_stt_ms"]["p95_ms"] == 9000
    assert hub2["total"] == 1 and hub2["deterministic_ratio"] == 0.0
    assert client.get("/api/system/turns/summary", params={"window_hours": 0}).status_code == 422


# --- 보존 기간 -----------------------------------------------------------------------------


def test_startup_purges_rows_older_than_retention(settings: Settings) -> None:
    tuned = settings.model_copy(update={"turn_log_days": 7})
    migrate(tuned.database_url)
    seed(
        tuned.database_url,
        [
            make_row("ancient", age=timedelta(days=30)),
            make_row("stale", age=timedelta(days=8)),
            make_row("fresh", age=timedelta(days=6)),
        ],
    )

    with TestClient(create_app(tuned)):
        pass

    assert count_rows(tuned.database_url) == ["fresh"]


def test_default_retention_is_30_days(settings: Settings) -> None:
    assert settings.turn_log_days == 30


# --- 저장과 로그 ---------------------------------------------------------------------------


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    fake.start()
    yield fake
    fake.close()


@pytest.fixture
def voice_client(ha_settings: Settings) -> Iterator[TestClient]:
    migrate(ha_settings.database_url)
    app = create_app(ha_settings, openai=FakeOpenAI(), clock=lambda: NOW)
    with TestClient(app) as client:
        eventually(lambda: client.app.state.home.ready)  # type: ignore[attr-defined]
        yield client


def test_finished_text_turn_is_stored_and_served(voice_client: TestClient) -> None:
    with connect_mobile(voice_client) as mobile:
        _text(mobile, "거실 조명 꺼줘")

    stored = eventually(lambda: voice_client.get("/api/system/turns").json())
    turn = stored[0]
    assert (turn["source"], turn["path"], turn["intent"]) == ("text", "matcher", "DeviceCommand")
    assert turn["transcript"] == "거실 조명 꺼줘" and turn["response_text"] == "거실 조명을 껐어요"
    assert turn["skill_result"] == "ok" and turn["error_code"] == ""
    assert turn["t_understand_ms"] is not None and turn["t_first_audio_ms"] is None
    summary = voice_client.get("/api/system/turns/summary").json()
    assert summary["total"] == 1 and summary["deterministic_ratio"] == 1.0


def test_persist_failure_does_not_break_the_turn(
    voice_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = voice_client.app.state.turns  # type: ignore[attr-defined]

    async def broken(*_: object, **__: object) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr("app.domain.telemetry.crud.insert", broken)
    with connect_mobile(voice_client) as mobile:
        _text(mobile, "거실 조명 꺼줘")
    eventually(lambda: not recorder._pending)

    assert voice_client.get("/api/system/turns").json() == []


def test_finish_logs_one_info_line_without_transcript_and_debug_with_it() -> None:
    lines: list[tuple[str, str]] = []
    sink = logger.add(
        lambda m: lines.append((m.record["level"].name, m.record["message"])), level="DEBUG"
    )
    try:

        async def run() -> None:
            from app.domain.telemetry.recorder import TurnRecorder

            recorder = TurnRecorder(lambda: None, 30)  # type: ignore[arg-type,return-value]
            turn = Turn("hub-1", TurnSource.VOICE, 0.0, turn_id="t1", transcript="비밀 문장")
            turn.path = TurnPath.MATCHER
            recorder.finish(turn)
            await recorder.drain()

        asyncio.run(run())
    finally:
        logger.remove(sink)

    info = [m for level, m in lines if level == "INFO" and m.startswith("Turn finished")]
    debug = [m for level, m in lines if level == "DEBUG" and m.startswith("Turn transcript")]
    assert len(info) == 1 and "turn_id=t1" in info[0] and "path=matcher" in info[0]
    assert "비밀 문장" not in info[0] and "비밀 문장" in debug[0]


def test_summary_reports_tts_cache_hit_ratio_over_audio_turns() -> None:
    rows = [
        make_row("a", first_audio=900, cache_hit=True),
        make_row("b", first_audio=1500),
        make_row("c", first_audio=800, cache_hit=True),
        make_row("silent", first_audio=None),
    ]

    summary = summarize(rows, 24)

    assert summary.tts_cache_hit_count == 2
    assert summary.tts_cache_hit_ratio == round(2 / 3, 4)


def test_declined_turns_do_not_dilute_deterministic_ratio() -> None:
    rows = [
        make_row("a", TurnPath.MATCHER, stt=600, understand=1, act=30, first_audio=1400),
        make_row("b", TurnPath.LLM, stt=800, understand=900, act=200, first_audio=2500),
        make_row("c", TurnPath.DECLINED, stt=700, understand=None, act=None, first_audio=None),
    ]

    summary = summarize(rows, 24)

    assert summary.paths["declined"].count == 1
    assert summary.deterministic_ratio == 0.5
