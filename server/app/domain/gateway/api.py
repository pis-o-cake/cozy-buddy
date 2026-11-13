"""게이트웨이 엔드포인트. 접두사 없이 /ws, /health 로 노출한다."""

from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket

from app.config import Settings
from app.core.connections import ConnectionManager, get_connections
from app.core.database import Database, get_database
from app.domain.gateway.service import GatewayService
from app.domain.home.service import HomeService
from app.domain.voice.service import VoiceService
from app.domain.weather.service import WeatherService

root_router = APIRouter()


def get_settings(websocket: WebSocket) -> Settings:
    """앱 설정 의존성."""
    settings: Settings = websocket.app.state.settings
    return settings


def get_home_service(websocket: WebSocket) -> HomeService:
    """HomeService 의존성."""
    home: HomeService = websocket.app.state.home
    return home


def get_weather_service(websocket: WebSocket) -> WeatherService:
    """WeatherService 의존성."""
    weather: WeatherService = websocket.app.state.weather
    return weather


def get_voice_service(websocket: WebSocket) -> VoiceService:
    """VoiceService 의존성."""
    voice: VoiceService = websocket.app.state.voice
    return voice


def get_gateway_service(
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
    connections: Annotated[ConnectionManager, Depends(get_connections)],
    home: Annotated[HomeService, Depends(get_home_service)],
    voice: Annotated[VoiceService, Depends(get_voice_service)],
    weather: Annotated[WeatherService, Depends(get_weather_service)],
) -> GatewayService:
    """GatewayService 의존성."""
    return GatewayService(settings, database, connections, home, voice, weather)


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
