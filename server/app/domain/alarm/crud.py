"""알람 사본 조회·저장. 커밋은 호출자(service)가 한다."""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.alarm.models import Alarm, SyncState


async def get(session: AsyncSession, hub_id: str, alarm_id: str) -> Alarm | None:
    """(hub_id, alarm_id) 로 알람을 찾는다."""
    return await session.get(Alarm, (hub_id, alarm_id))


async def list_alarms(session: AsyncSession, hub_id: str | None = None) -> Sequence[Alarm]:
    """알람을 (hub_id, alarm_id) 순으로 돌려준다. hub_id 가 있으면 그 허브만."""
    stmt = select(Alarm).order_by(Alarm.hub_id, Alarm.alarm_id)
    if hub_id is not None:
        stmt = stmt.where(Alarm.hub_id == hub_id)
    return (await session.scalars(stmt)).all()


async def list_pending(session: AsyncSession, hub_id: str) -> Sequence[Alarm]:
    """허브에 아직 전달·확인되지 않은 알람."""
    stmt = (
        select(Alarm)
        .where(Alarm.hub_id == hub_id, Alarm.sync_state != SyncState.SYNCED)
        .order_by(Alarm.alarm_id)
    )
    return (await session.scalars(stmt)).all()


async def delete_synced(session: AsyncSession, hub_id: str) -> None:
    """허브의 synced 사본을 모두 지운다. pending 행은 남긴다."""
    await session.execute(
        delete(Alarm).where(Alarm.hub_id == hub_id, Alarm.sync_state == SyncState.SYNCED)
    )


async def delete_row(session: AsyncSession, alarm: Alarm) -> None:
    """알람 행 하나를 지운다."""
    await session.delete(alarm)
