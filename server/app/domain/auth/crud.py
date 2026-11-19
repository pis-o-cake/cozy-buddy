"""클라이언트 토큰 조회·저장."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.auth.models import ClientToken
from app.domain.hub.models import Hub


async def add(session: AsyncSession, token: ClientToken) -> None:
    """토큰을 추가하고 커밋한다."""
    session.add(token)
    await session.commit()


async def list_active(session: AsyncSession, client_id: str) -> Sequence[ClientToken]:
    """폐기되지 않은 토큰을 client_id 로 찾는다."""
    result = await session.scalars(
        select(ClientToken).where(
            ClientToken.client_id == client_id, ClientToken.revoked_at.is_(None)
        )
    )
    return result.all()


async def revoke_client(session: AsyncSession, client_id: str, now: datetime) -> int:
    """클라이언트의 활성 토큰을 모두 폐기한다.

    Returns:
        폐기한 토큰 수.
    """
    active = await list_active(session, client_id)
    for token in active:
        token.revoked_at = now
    await session.commit()
    return len(active)


async def list_tokens(session: AsyncSession) -> Sequence[ClientToken]:
    """모든 토큰 행을 발급 순으로 돌려준다."""
    result = await session.scalars(select(ClientToken).order_by(ClientToken.created_at))
    return result.all()


async def hubs_by_id(session: AsyncSession, hub_ids: set[str]) -> dict[str, Hub]:
    """hub_id 집합에 해당하는 허브를 ID 로 묶어 돌려준다."""
    if not hub_ids:
        return {}
    result = await session.scalars(select(Hub).where(Hub.hub_id.in_(hub_ids)))
    return {hub.hub_id: hub for hub in result.all()}
