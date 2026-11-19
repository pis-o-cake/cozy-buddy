"""시스템 상태 REST API. /api/system/backup."""

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.maintenance import BackupScheduler

root_router = APIRouter(prefix="/api/system")


class BackupStatus(BaseModel):
    """DB 백업 현황."""

    enabled: bool
    last_backup_at: datetime | None
    last_backup_size_bytes: int | None


@root_router.get("/backup")
async def backup_status(request: Request) -> BackupStatus:
    """가장 최근 DB 백업의 시각과 크기. 백업이 꺼져 있거나 아직 없으면 값이 비어 있다."""
    scheduler: BackupScheduler = request.app.state.backups
    latest = scheduler.latest()
    return BackupStatus(
        enabled=scheduler.active,
        last_backup_at=latest.created_at if latest else None,
        last_backup_size_bytes=latest.size_bytes if latest else None,
    )
