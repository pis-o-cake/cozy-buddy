"""턴 로그 REST 스키마."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TurnRead(BaseModel):
    """턴 로그 한 건."""

    model_config = ConfigDict(from_attributes=True)

    turn_id: str
    hub_id: str
    source: str
    transcript: str
    path: str
    intent: str
    skill_result: str
    response_text: str
    t_stt_ms: int | None
    t_understand_ms: int | None
    t_act_ms: int | None
    t_first_audio_ms: int | None
    llm_tool_calls: int
    stt_hedged: bool
    error_code: str
    created_at: datetime


class PathStat(BaseModel):
    """경로 하나의 건수와 전체 대비 비율."""

    count: int
    ratio: float


class StageStat(BaseModel):
    """단계 시간 분포. 값이 없는 턴은 센 건수에서 빠진다. 백분위는 최근접 순위법이다."""

    count: int
    p50_ms: int | None
    p95_ms: int | None


class TurnSummary(BaseModel):
    """윈도 안의 경로 비율과 단계별 지연.

    deterministic_ratio 는 LLM 없이 처리한 턴(context·matcher·weather·clarify)의 비율이다.
    stt_hedge_ratio 는 STT 를 거친 턴(t_stt_ms 가 있는 턴) 중 헤지 요청을 보낸 턴의 비율이다.
    """

    window_hours: float
    total: int
    deterministic_ratio: float
    stt_hedged_count: int
    stt_hedge_ratio: float
    paths: dict[str, PathStat]
    stages: dict[str, StageStat]
