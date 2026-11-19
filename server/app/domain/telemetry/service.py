"""턴 로그 조회 서비스. 요청 범위 세션 위에서 동작한다."""

from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.domain.telemetry import crud
from app.domain.telemetry.models import TurnLog
from app.domain.telemetry.schemas import TurnSummary
from app.domain.telemetry.summary import summarize


class TurnQueryService:
    """저장된 턴을 읽어 목록과 요약을 만든다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recent(self, hub_id: str | None, limit: int) -> Sequence[TurnLog]:
        """최근 턴을 최신순으로 돌려준다."""
        return await crud.list_recent(self._session, hub_id, limit)

    async def summary(self, window_hours: float, hub_id: str | None = None) -> TurnSummary:
        """최근 window_hours 시간의 경로 비율과 단계별 p50/p95."""
        since = utcnow() - timedelta(hours=window_hours)
        rows = await crud.list_since(self._session, since, hub_id)
        return summarize(rows, window_hours)
