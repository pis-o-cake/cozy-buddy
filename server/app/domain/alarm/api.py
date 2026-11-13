"""알람 REST API. /api/alarm."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connections import ConnectionManager, get_connections
from app.core.database import get_session
from app.domain.alarm.schemas import AlarmRead
from app.domain.alarm.service import AlarmService
from app.domain.hub.service import HubService

router = APIRouter()


def get_alarm_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    connections: Annotated[ConnectionManager, Depends(get_connections)],
) -> AlarmService:
    """AlarmService 의존성."""
    return AlarmService(session, connections, HubService(session, connections))


@router.get("")
async def list_alarms(
    service: Annotated[AlarmService, Depends(get_alarm_service)], hub_id: str | None = None
) -> list[AlarmRead]:
    """서버가 보관한 알람 사본과 sync_state. hub_id 로 허브를 좁힌다."""
    return await service.list_alarms(hub_id)
