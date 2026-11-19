"""발화 한 건(턴)의 진행 기록. 처음부터 끝까지 같은 객체를 들고 다니며 단계마다 채운다."""

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

MAX_RESPONSE_CHARS = 1000


class TurnSource(StrEnum):
    """턴이 시작된 경로."""

    VOICE = "voice"
    TEXT = "text"


class TurnPath(StrEnum):
    """응답을 만든 경로. S9(결정론 커버리지)는 이 값의 비율로 판단한다."""

    CONTEXT = "context"
    MATCHER = "matcher"
    WEATHER = "weather"
    LLM = "llm"
    CLARIFY = "clarify"
    ERROR = "error"


class SkillOutcome(StrEnum):
    """스킬 실행 결과 요약."""

    OK = "ok"
    FAILED = "failed"
    NONE = "none"


class Stage(StrEnum):
    """시간을 재는 단계. 값은 Turn 의 `t_<stage>_ms` 필드 이름에 쓴다."""

    STT = "stt"
    UNDERSTAND = "understand"
    ACT = "act"


@dataclass(slots=True)
class Turn:
    """턴 기록. 필드는 `turn_log` 테이블 행과 같다.

    시각은 모두 `started_at`(발화 종료 또는 TextQuery 도착) 기준의 경과 시간(ms)이다.
    단계 시간은 단계별로 더해 가므로 LLM 처럼 여러 번 오가는 단계도 한 값으로 남는다.
    """

    hub_id: str
    source: TurnSource
    started_at: float
    clock: Callable[[], float] = time.monotonic
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    transcript: str = ""
    path: TurnPath | None = None
    intent: str = ""
    skill_result: SkillOutcome = SkillOutcome.NONE
    response_text: str = ""
    t_stt_ms: int | None = None
    t_understand_ms: int | None = None
    t_act_ms: int | None = None
    t_first_audio_ms: int | None = None
    llm_tool_calls: int = 0
    stt_hedged: bool = False
    tts_cache_hit: bool = False
    error_code: str = ""

    def add_seconds(self, stage: Stage, seconds: float) -> None:
        """단계 시간에 경과 시간을 더한다."""
        name = f"t_{stage.value}_ms"
        current = getattr(self, name) or 0
        setattr(self, name, current + max(0, round(seconds * 1000)))

    @contextmanager
    def timed(self, stage: Stage) -> Iterator[None]:
        """블록이 걸린 시간을 단계 시간에 더한다. 예외가 나도 더한다."""
        began = self.clock()
        try:
            yield
        finally:
            self.add_seconds(stage, self.clock() - began)

    def mark_first_audio(self, at: float) -> None:
        """첫 오디오를 보낸 monotonic 시각을 기록한다. 이미 기록돼 있으면 유지한다."""
        if self.t_first_audio_ms is None:
            self.t_first_audio_ms = max(0, round((at - self.started_at) * 1000))

    def record_skill(self, ok: bool) -> None:
        """스킬 결과를 반영한다. 한 번이라도 실패하면 failed 로 남는다."""
        if not ok:
            self.skill_result = SkillOutcome.FAILED
        elif self.skill_result is SkillOutcome.NONE:
            self.skill_result = SkillOutcome.OK

    def append_response(self, delta: str) -> None:
        """응답 텍스트 조각을 이어 붙인다. 너무 길면 자른다."""
        room = MAX_RESPONSE_CHARS - len(self.response_text)
        if room > 0:
            self.response_text += delta[:room]

    def fail(self, error_code: str) -> None:
        """오류 코드를 남긴다. 먼저 남긴 코드를 덮어쓰지 않는다."""
        if not self.error_code:
            self.error_code = error_code
