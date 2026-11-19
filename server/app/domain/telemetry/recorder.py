"""턴 기록기: 턴을 시작·종료하고, 끝난 턴을 로그와 DB 에 남기고, 오래된 기록을 지운다."""

import asyncio
import contextlib
import time
from collections.abc import Callable
from datetime import timedelta

from loguru import logger

from app.core.database import Database, utcnow
from app.domain.telemetry import crud
from app.domain.telemetry.models import TurnLog
from app.domain.telemetry.turn import Turn, TurnPath, TurnSource

PURGE_INTERVAL_SECONDS = 24 * 3600
DRAIN_TIMEOUT_SECONDS = 5.0


class TurnRecorder:
    """앱 수명 동안 하나만 존재한다. 저장 실패는 로그만 남기고 대화를 막지 않는다."""

    def __init__(
        self,
        database: Callable[[], Database],
        retention_days: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._database = database
        self._retention = timedelta(days=retention_days)
        self._clock = clock
        self._pending: set[asyncio.Task[None]] = set()
        self._purge_task: asyncio.Task[None] | None = None

    def begin(self, hub_id: str, source: TurnSource, started_at: float | None = None) -> Turn:
        """턴을 시작한다. started_at 은 지연의 기준이 되는 monotonic 시각(기본은 지금)이다."""
        return Turn(
            hub_id=hub_id,
            source=source,
            started_at=self._clock() if started_at is None else started_at,
            clock=self._clock,
        )

    def finish(self, turn: Turn) -> None:
        """끝난 턴을 구조화 로그 한 줄로 남기고 저장을 예약한다. 기다리지 않는다."""
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
        task = asyncio.create_task(self._persist(turn), name="turn-log-persist")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self) -> None:
        """예약된 저장이 모두 끝날 때까지 기다린다."""
        pending = list(self._pending)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def purge(self) -> int:
        """보존 기간이 지난 턴을 지운다. 실패하면 0 을 돌려주고 로그만 남긴다."""
        cutoff = utcnow() - self._retention
        try:
            async with self._database().session() as session:
                removed = await crud.delete_before(session, cutoff)
                await session.commit()
        except Exception:
            logger.opt(exception=True).warning("Turn log purge failed")
            return 0
        logger.info("Turn log purged: removed={} retention_days={}", removed, self._retention.days)
        return removed

    async def start(self) -> None:
        """시작 시 한 번 정리하고 이후 하루에 한 번 정리하는 태스크를 띄운다."""
        await self.purge()
        self._purge_task = asyncio.create_task(self._purge_daily(), name="turn-log-purge")

    async def stop(self) -> None:
        """정리 태스크를 멈추고 남은 저장을 마친다."""
        task, self._purge_task = self._purge_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(DRAIN_TIMEOUT_SECONDS):
                await self.drain()

    async def _purge_daily(self) -> None:
        while True:
            await asyncio.sleep(PURGE_INTERVAL_SECONDS)
            await self.purge()

    async def _persist(self, turn: Turn) -> None:
        row = TurnLog(
            turn_id=turn.turn_id,
            hub_id=turn.hub_id,
            source=turn.source.value,
            transcript=turn.transcript,
            path=turn.path.value if turn.path else TurnPath.ERROR.value,
            intent=turn.intent,
            skill_result=turn.skill_result.value,
            response_text=turn.response_text,
            t_stt_ms=turn.t_stt_ms,
            t_understand_ms=turn.t_understand_ms,
            t_act_ms=turn.t_act_ms,
            t_first_audio_ms=turn.t_first_audio_ms,
            llm_tool_calls=turn.llm_tool_calls,
            error_code=turn.error_code,
            created_at=turn.created_at,
        )
        try:
            async with self._database().session() as session:
                await crud.insert(session, row)
                await session.commit()
        except Exception:
            logger.opt(exception=True).warning("Turn log write failed: turn_id={}", turn.turn_id)
