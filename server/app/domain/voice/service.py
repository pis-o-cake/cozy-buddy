"""음성 서비스: 연결별 VoiceSession 을 만들고 수명을 관리한다."""

from collections.abc import Callable

from app.config import ClientRole, Settings
from app.core.connections import ClientConnection, ConnectionManager
from app.core.database import Database
from app.core.i18n import Translator
from app.core.openai_gateway import OpenAIGateway
from app.domain.agent.service import AgentService
from app.domain.hub import crud as hub_crud
from app.domain.voice.session import VoiceDeps, VoiceSession


class VoiceService:
    """앱 수명 동안 하나만 존재한다. 연결 하나당 VoiceSession 하나를 둔다."""

    def __init__(
        self,
        settings: Settings,
        translate: Translator,
        agent: AgentService,
        openai: OpenAIGateway | None,
        connections: ConnectionManager,
        database: Callable[[], Database],
    ) -> None:
        self._connections = connections
        self._database = database
        self._sessions: dict[ClientConnection, VoiceSession] = {}
        self._deps = VoiceDeps(
            settings=settings,
            translate=translate,
            agent=agent,
            openai=openai,
            hub_area=self._hub_area,
            resolve_hub=self._resolve_hub,
        )

    def session_for(self, conn: ClientConnection) -> VoiceSession:
        """연결의 음성 세션. 없으면 만든다."""
        session = self._sessions.get(conn)
        if session is None:
            session = self._sessions[conn] = VoiceSession(conn, self._deps)
        return session

    async def close(self, conn: ClientConnection) -> None:
        """연결이 끊겼을 때 세션과 진행 중인 작업을 정리한다."""
        session = self._sessions.pop(conn, None)
        if session is not None:
            await session.close()

    async def _hub_area(self, hub_id: str) -> str:
        if not hub_id:
            return ""
        async with self._database().session() as session:
            hub = await hub_crud.get(session, hub_id)
        return hub.area_id if hub is not None else ""

    def _resolve_hub(self, conn: ClientConnection) -> str:
        """발화한 허브 ID. 앱은 허브가 정확히 하나 연결돼 있을 때만 그 허브로 본다."""
        if conn.role == ClientRole.HUB:
            return conn.client_id
        online = self._connections.online_hub_ids()
        return next(iter(online)) if len(online) == 1 else ""
