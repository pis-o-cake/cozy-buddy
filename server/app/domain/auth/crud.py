"""클라이언트 토큰 조회·저장."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.auth.models import ClientToken


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
