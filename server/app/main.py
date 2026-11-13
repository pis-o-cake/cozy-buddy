"""FastAPI 앱 팩토리. 실행: gunicorn "app.main:create_app()"."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import APIRouter, FastAPI
from loguru import logger

from app.config import Settings
from app.core.connections import ConnectionManager
from app.core.database import Database
from app.core.discovery import discover_domain_modules
from app.core.exceptions import register_exception_handlers
from app.core.i18n import Translator
from app.core.logging import configure_logging
from app.core.openai_gateway import OpenAIGateway
from app.core.version import get_server_version
from app.domain.agent.service import AgentService
from app.domain.home.service import HomeService
from app.domain.voice.service import VoiceService
from app.middleware.request_log import RequestLogMiddleware


def _include_domain_routers(app: FastAPI) -> None:
    """app/domain/*/api.py 를 스캔해 등록한다.

    `router` 는 /api/<domain> 에, `root_router` 는 접두사 없이(/ws, /health 등) 붙는다.
    """
    for name, module in discover_domain_modules("api"):
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router, prefix=f"/api/{name}", tags=[name])
        root_router = getattr(module, "root_router", None)
        if isinstance(root_router, APIRouter):
            app.include_router(root_router, tags=[name])


def create_app(
    settings: Settings | None = None,
    openai: OpenAIGateway | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """앱을 만든다.

    Args:
        settings: 주입할 설정. 없으면 환경변수와 .env 에서 읽는다.
        openai: 주입할 OpenAI 관문(테스트용 가짜). 없으면 설정에 키가 있을 때 만든다.
        clock: 주입할 현재 시각 함수(테스트용). 없으면 설정의 시간대로 시스템 시각을 쓴다.

    Returns:
        구성이 끝난 FastAPI 앱.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.db = Database(settings.database_url)
        logger.info("Server started: version={}", get_server_version())
        await app.state.home.start()
        try:
            yield
        finally:
            await app.state.home.stop()
            if gateway is not None:
                await gateway.aclose()
            await app.state.db.dispose()
            logger.info("Server stopped")

    app = FastAPI(title="Cozy Server", version=get_server_version(), lifespan=lifespan)
    app.state.settings = settings
    app.state.translator = Translator(settings.locale)
    # WARNING: 연결 레지스트리는 프로세스 메모리에 있으므로 워커는 1개만 쓴다.
    app.state.connections = ConnectionManager()
    app.state.home = HomeService(settings, app.state.connections)
    gateway = openai or (OpenAIGateway(settings) if settings.voice_enabled else None)
    if gateway is None:
        logger.info("Voice pipeline disabled: OPENAI_API_KEY not set")

    def get_db() -> Database:
        db: Database = app.state.db
        return db

    app.state.agent = AgentService(
        settings,
        app.state.translator,
        app.state.home,
        app.state.connections,
        get_db,
        gateway,
        clock,
    )
    app.state.voice = VoiceService(
        settings, app.state.translator, app.state.agent, gateway, app.state.connections, get_db
    )
    app.add_middleware(RequestLogMiddleware)
    register_exception_handlers(app)
    _include_domain_routers(app)
    return app
