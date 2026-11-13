"""허브 조회·저장."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.hub.models import Hub


async def get(session: AsyncSession, hub_id: str) -> Hub | None:
    """ID 로 허브를 찾는다."""
    return await session.get(Hub, hub_id)


async def list_all(session: AsyncSession) -> Sequence[Hub]:
    """허브 전체를 ID 순으로 돌려준다."""
    result = await session.scalars(select(Hub).order_by(Hub.hub_id))
    return result.all()


async def add(session: AsyncSession, hub: Hub) -> Hub:
    """허브를 추가하고 커밋한다."""
    session.add(hub)
    await session.commit()
    return hub
