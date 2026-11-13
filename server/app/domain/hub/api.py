"""허브 REST API. /api/hub."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connections import ConnectionManager, get_connections
from app.core.database import get_session
from app.domain.hub.schemas import HubRead, HubUpdate
from app.domain.hub.service import HubService

router = APIRouter()


def get_hub_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    connections: Annotated[ConnectionManager, Depends(get_connections)],
) -> HubService:
    """HubService 의존성."""
    return HubService(session, connections)


HubServiceDep = Annotated[HubService, Depends(get_hub_service)]


@router.get("")
async def list_hubs(service: HubServiceDep) -> list[HubRead]:
    """허브 목록과 연결 여부."""
    return await service.list_hubs()


@router.patch("/{hub_id}")
async def update_hub(hub_id: str, body: HubUpdate, service: HubServiceDep) -> HubRead:
    """허브 이름·방(area_id) 수정."""
    return await service.update(hub_id, body)
