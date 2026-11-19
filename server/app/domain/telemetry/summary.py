"""턴 로그 집계: 경로 비율과 단계별 백분위."""

import math
from collections.abc import Iterable, Sequence

from app.domain.telemetry.models import TurnLog
from app.domain.telemetry.schemas import PathStat, StageStat, TurnSummary
from app.domain.telemetry.turn import Stage, TurnPath

# LLM 을 거치지 않고 처리한 경로. error 는 처리하지 못한 턴이므로 넣지 않는다.
DETERMINISTIC_PATHS = frozenset(
    {TurnPath.CONTEXT, TurnPath.MATCHER, TurnPath.WEATHER, TurnPath.CLARIFY}
)
STAGE_FIELDS = tuple(f"t_{s.value}_ms" for s in Stage) + ("t_first_audio_ms",)


def percentile(values: Sequence[int], pct: float) -> int | None:
    """최근접 순위법 백분위. 값이 없으면 None.

    순위 = ceil(pct/100 * n) 번째로 작은 값이다. 보간하지 않으므로 실제 측정값만 나온다.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(rows: Iterable[TurnLog], window_hours: float) -> TurnSummary:
    """턴 목록을 요약한다. 경로는 값이 0건이어도 모두 싣는다."""
    turns = list(rows)
    total = len(turns)
    counts = {path: 0 for path in TurnPath}
    for row in turns:
        counts[TurnPath(row.path)] += 1
    paths = {
        path.value: PathStat(count=n, ratio=round(n / total, 4) if total else 0.0)
        for path, n in counts.items()
    }
    deterministic = sum(n for path, n in counts.items() if path in DETERMINISTIC_PATHS)
    # 거절된 턴은 응답하지 않았으므로 결정론 비율의 분모에서 뺀다.
    answered = total - counts[TurnPath.DECLINED]
    stages: dict[str, StageStat] = {}
    for name in STAGE_FIELDS:
        values = [v for row in turns if (v := getattr(row, name)) is not None]
        stages[name] = StageStat(
            count=len(values), p50_ms=percentile(values, 50), p95_ms=percentile(values, 95)
        )
    stt_turns = sum(1 for row in turns if row.t_stt_ms is not None)
    hedged = sum(1 for row in turns if row.stt_hedged)
    audio_turns = sum(1 for row in turns if row.t_first_audio_ms is not None)
    cache_hits = sum(1 for row in turns if row.tts_cache_hit)
    return TurnSummary(
        window_hours=window_hours,
        total=total,
        deterministic_ratio=round(deterministic / answered, 4) if answered else 0.0,
        stt_hedged_count=hedged,
        stt_hedge_ratio=round(hedged / stt_turns, 4) if stt_turns else 0.0,
        tts_cache_hit_count=cache_hits,
        tts_cache_hit_ratio=round(cache_hits / audio_turns, 4) if audio_turns else 0.0,
        paths=paths,
        stages=stages,
    )
