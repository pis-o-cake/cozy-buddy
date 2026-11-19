"""턴 로그 조회·저장. 커밋은 호출자가 한다."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.telemetry.models import TurnLog


async def insert(session: AsyncSession, row: TurnLog) -> None:
    """턴 한 건을 추가한다."""
    session.add(row)


async def list_recent(session: AsyncSession, hub_id: str | None, limit: int) -> Sequence[TurnLog]:
    """최근 턴을 최신순으로 돌려준다. hub_id 가 있으면 그 허브만."""
    stmt = select(TurnLog).order_by(TurnLog.created_at.desc(), TurnLog.turn_id).limit(limit)
    if hub_id is not None:
        stmt = stmt.where(TurnLog.hub_id == hub_id)
    return (await session.scalars(stmt)).all()


async def list_since(
    session: AsyncSession, since: datetime, hub_id: str | None = None
) -> Sequence[TurnLog]:
    """since 이후 턴을 오래된 순으로 돌려준다."""
    stmt = select(TurnLog).where(TurnLog.created_at >= since).order_by(TurnLog.created_at)
    if hub_id is not None:
        stmt = stmt.where(TurnLog.hub_id == hub_id)
    return (await session.scalars(stmt)).all()


async def delete_before(session: AsyncSession, cutoff: datetime) -> int:
    """cutoff 이전 턴을 지우고 지운 수를 돌려준다."""
    result = await session.execute(delete(TurnLog).where(TurnLog.created_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)
