"""게이트웨이 엔드포인트. 접두사 없이 /ws, /health 로 노출한다."""

from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket

from app.config import Settings
from app.core.connections import ConnectionManager, get_connections
from app.core.database import Database, get_database
from app.domain.gateway.service import GatewayService

root_router = APIRouter()


def get_settings(websocket: WebSocket) -> Settings:
    """앱 설정 의존성."""
    settings: Settings = websocket.app.state.settings
    return settings


def get_gateway_service(
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
    connections: Annotated[ConnectionManager, Depends(get_connections)],
) -> GatewayService:
    """GatewayService 의존성."""
    return GatewayService(settings, database, connections)


@root_router.get("/health")
async def health() -> dict[str, str]:
    """생존 확인."""
    return {"status": "ok"}


@root_router.websocket("/ws")
async def ws_endpoint(
    websocket: WebSocket, service: Annotated[GatewayService, Depends(get_gateway_service)]
) -> None:
    """허브·앱 WebSocket. 모든 프레임은 cozy.v1.Envelope 바이너리다."""
    await service.serve(websocket)
