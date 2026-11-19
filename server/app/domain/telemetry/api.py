"""턴 로그 REST API. /api/system/turns.

도메인 이름(telemetry) 대신 /api/system 아래에 노출하려고 `router` 가 아니라 `root_router` 를 쓴다.
자동 등록은 root_router 를 접두사 없이 붙이므로 경로 전체를 여기서 정한다. 별도 system 도메인을
만들면 얇은 위임 계층만 늘어나서, 데이터를 가진 telemetry 가 직접 내보낸다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.domain.telemetry.schemas import TurnRead, TurnSummary
from app.domain.telemetry.service import TurnQueryService

root_router = APIRouter(prefix="/api/system")

MAX_LIMIT = 500
MAX_WINDOW_HOURS = 24 * 90


def get_turn_query_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TurnQueryService:
    """TurnQueryService 의존성."""
    return TurnQueryService(session)


@root_router.get("/turns")
async def list_turns(
    service: Annotated[TurnQueryService, Depends(get_turn_query_service)],
    hub_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> list[TurnRead]:
    """최근 턴을 최신순으로 돌려준다. hub_id 로 허브를 좁힌다."""
    return [TurnRead.model_validate(row) for row in await service.recent(hub_id, limit)]


@root_router.get("/turns/summary")
async def turn_summary(
    service: Annotated[TurnQueryService, Depends(get_turn_query_service)],
    window_hours: Annotated[float, Query(gt=0, le=MAX_WINDOW_HOURS)] = 24.0,
    hub_id: str | None = None,
) -> TurnSummary:
    """최근 window_hours 시간의 경로별 비율과 단계별 p50/p95."""
    return await service.summary(window_hours, hub_id)
