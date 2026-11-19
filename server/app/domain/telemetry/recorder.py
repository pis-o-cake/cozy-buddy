"""턴 기록기: 턴을 시작하고 끝난 턴을 구조화 로그 한 줄로 남긴다."""

import time
from collections.abc import Callable

from loguru import logger

from app.domain.telemetry.turn import Turn, TurnPath, TurnSource


class TurnRecorder:
    """앱 수명 동안 하나만 존재한다."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def begin(self, hub_id: str, source: TurnSource, started_at: float | None = None) -> Turn:
        """턴을 시작한다. started_at 은 지연의 기준이 되는 monotonic 시각(기본은 지금)이다."""
        return Turn(
            hub_id=hub_id,
            source=source,
            started_at=self._clock() if started_at is None else started_at,
            clock=self._clock,
        )

    def finish(self, turn: Turn) -> None:
        """끝난 턴을 구조화 로그 한 줄로 남긴다. 전사문은 디버그 수준에서만 남긴다."""
        if turn.path is None:
            turn.path = TurnPath.ERROR
            turn.fail("no_path")
        logger.info(
            "Turn finished: turn_id={} hub_id={} source={} path={} intent={} skill={} "
            "stt_ms={} understand_ms={} act_ms={} first_audio_ms={} tool_calls={} error={}",
            turn.turn_id,
            turn.hub_id,
            turn.source.value,
            turn.path.value,
            turn.intent or "-",
            turn.skill_result.value,
            turn.t_stt_ms,
            turn.t_understand_ms,
            turn.t_act_ms,
            turn.t_first_audio_ms,
            turn.llm_tool_calls,
            turn.error_code or "-",
        )
        logger.debug("Turn transcript: turn_id={} transcript={}", turn.turn_id, turn.transcript)
