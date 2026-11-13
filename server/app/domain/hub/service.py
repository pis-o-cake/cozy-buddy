"""허브 비즈니스 로직."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connections import ConnectionManager
from app.core.database import utcnow
from app.core.exceptions import NotFoundError
from app.domain.hub import crud
from app.domain.hub.models import Hub
from app.domain.hub.schemas import HubRead, HubUpdate


class HubService:
    """허브 등록·갱신·조회."""

    def __init__(self, session: AsyncSession, connections: ConnectionManager) -> None:
        self._session = session
        self._connections = connections

    async def ensure_registered(self, hub_id: str) -> Hub:
        """허브가 없으면 만들고, last_seen_at 을 현재로 갱신한다."""
        hub = await crud.get(self._session, hub_id)
        if hub is None:
            return await crud.add(self._session, Hub(hub_id=hub_id, last_seen_at=utcnow()))
        hub.last_seen_at = utcnow()
        await self._session.commit()
        return hub

    async def touch(self, hub_id: str) -> None:
        """last_seen_at 만 갱신한다. 없는 허브는 무시한다."""
        hub = await crud.get(self._session, hub_id)
        if hub is None:
            return
        hub.last_seen_at = utcnow()
        await self._session.commit()

    async def exists(self, hub_id: str) -> bool:
        """허브가 등록돼 있는지."""
        return await crud.get(self._session, hub_id) is not None

    async def list_hubs(self) -> list[HubRead]:
        """연결 여부를 포함한 허브 목록."""
        online = self._connections.online_hub_ids()
        return [
            HubRead.model_validate(hub).model_copy(update={"online": hub.hub_id in online})
            for hub in await crud.list_all(self._session)
        ]

    async def update(self, hub_id: str, data: HubUpdate) -> HubRead:
        """이름·방을 수정한다.

        Raises:
            NotFoundError: 허브가 없을 때.
        """
        hub = await crud.get(self._session, hub_id)
        if hub is None:
            raise NotFoundError(f"Hub not found: {hub_id}", "hub.not_found")
        for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(hub, field, value)
        await self._session.commit()
        return HubRead.model_validate(hub).model_copy(
            update={"online": self._connections.is_hub_online(hub_id)}
        )
