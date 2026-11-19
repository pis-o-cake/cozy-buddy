"""주기 유지보수: SQLite 일일 백업과 보존 개수 정리.

턴 로그 보존 정리는 TurnRecorder 가 이미 시작 시와 하루마다 수행하므로 여기서 다시 하지 않는다.
"""

import asyncio
import contextlib
import os
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.engine import make_url

from app.config import Settings

BACKUP_PREFIX = "cozy-"
BACKUP_SUFFIX = ".db"
TEMP_SUFFIX = ".tmp"
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
CATCH_UP_WINDOW = timedelta(hours=24)
STOP_TIMEOUT_SECONDS = 60.0

Clock = Callable[[], datetime]
Waiter = Callable[[float], Awaitable[bool]]


class BackupError(Exception):
    """백업 생성·검증·복원 실패."""


@dataclass(frozen=True)
class BackupInfo:
    """백업 파일 한 건."""

    path: Path
    created_at: datetime
    size_bytes: int


def resolve_db_path(database_url: str) -> Path | None:
    """SQLite 파일 경로를 돌려준다. 파일 DB 가 아니면(인메모리·타 드라이버) None."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:" or database.startswith("file::memory:"):
        return None
    return Path(database)


def resolve_backup_dir(settings: Settings, db_path: Path) -> Path:
    """백업 디렉터리. 설정이 비어 있으면 DB 옆 backups/ (데이터 볼륨 안)."""
    configured = settings.backup_dir.strip()
    return Path(configured) if configured else db_path.parent / "backups"


def list_backups(backup_dir: Path) -> list[BackupInfo]:
    """백업을 최신순으로 나열한다. 이름 형식이 아닌 파일과 임시 파일은 무시한다."""
    if not backup_dir.is_dir():
        return []
    found: list[BackupInfo] = []
    for path in backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        stem = path.name[len(BACKUP_PREFIX) : -len(BACKUP_SUFFIX)]
        try:
            created = datetime.strptime(stem, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
            size = path.stat().st_size
        except (ValueError, OSError):
            continue
        found.append(BackupInfo(path, created, size))
    return sorted(found, key=lambda b: b.created_at, reverse=True)


def verify_database(path: Path) -> None:
    """`PRAGMA integrity_check` 가 ok 가 아니면 BackupError."""
    try:
        conn = sqlite3.connect(path, timeout=0)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f"Cannot check database: {exc}") from exc
    if rows != [("ok",)]:
        raise BackupError(f"Integrity check failed: {rows[:3]}")


def next_run_after(now: datetime, hour: int, tz: ZoneInfo) -> datetime:
    """now 이후 첫 현지 `hour:00` 시각(UTC, tz-aware). 정각이면 다음 날로 넘긴다."""
    local = now.astimezone(tz)
    candidate = datetime(local.year, local.month, local.day, hour, tzinfo=tz)
    if candidate.astimezone(UTC) <= now:
        next_day = local.date() + timedelta(days=1)
        candidate = datetime(next_day.year, next_day.month, next_day.day, hour, tzinfo=tz)
    return candidate.astimezone(UTC)


def _copy_online(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            # IMPORTANT: 백업본은 단일 파일이어야 한다(WAL 이면 -wal/-shm 이 따라붙는다).
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def create_backup(db_path: Path, backup_dir: Path, now: datetime) -> BackupInfo:
    """DB 를 backup_dir 에 백업한다. 임시 이름으로 쓰고 검증한 뒤 원자적으로 개명한다.

    Intent: SQLite 온라인 백업 API 를 쓴다. 쓰기 중인 WAL DB 에서 일관된 스냅샷을 주고(읽기
    트랜잭션 하나) 앱의 쓰기를 막지 않는다. 파일 복사는 -wal 을 놓쳐 깨질 수 있다.

    Raises:
        BackupError: 원본이 없거나 복사·검증에 실패했을 때.
    """
    if not db_path.is_file():
        raise BackupError(f"Database file not found: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    name = f"{BACKUP_PREFIX}{now.astimezone(UTC).strftime(TIMESTAMP_FORMAT)}{BACKUP_SUFFIX}"
    final = backup_dir / name
    temp = backup_dir / f".{name}{TEMP_SUFFIX}"
    try:
        _copy_online(db_path, temp)
        verify_database(temp)
        os.replace(temp, final)
    except (sqlite3.Error, OSError) as exc:
        raise BackupError(f"Backup failed: {exc}") from exc
    finally:
        temp.unlink(missing_ok=True)
    return BackupInfo(final, now.astimezone(UTC), final.stat().st_size)


def prune_backups(backup_dir: Path, keep: int) -> int:
    """최신 keep 개만 남기고 지운다. 남은 임시 파일도 정리한다. 지운 백업 수를 돌려준다."""
    for stale in backup_dir.glob(f".{BACKUP_PREFIX}*{TEMP_SUFFIX}"):
        stale.unlink(missing_ok=True)
    removed = 0
    for old in list_backups(backup_dir)[keep:]:
        old.path.unlink(missing_ok=True)
        removed += 1
    return removed


class BackupScheduler:
    """하루 한 번(현지 backup_hour) 백업하고, 시작 시 24시간 넘게 백업이 없으면 즉시 한 번 한다."""

    def __init__(self, settings: Settings, clock: Clock | None = None) -> None:
        self._enabled = settings.backup_enabled
        self._hour = settings.backup_hour
        self._keep = settings.backup_keep
        self._tz = ZoneInfo(settings.timezone)
        self._db_path = resolve_db_path(settings.database_url)
        self._backup_dir = (
            resolve_backup_dir(settings, self._db_path) if self._db_path is not None else None
        )
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.waiter: Waiter = self._wait

    @property
    def active(self) -> bool:
        """백업이 실제로 동작하는지(켜져 있고 파일 DB 일 때)."""
        return self._enabled and self._db_path is not None

    def latest(self) -> BackupInfo | None:
        """가장 최근 백업. 없거나 비활성이면 None."""
        if self._backup_dir is None:
            return None
        backups = list_backups(self._backup_dir)
        return backups[0] if backups else None

    async def run_backup(self) -> BackupInfo | None:
        """백업 한 번과 보존 정리. 실패하면 로그만 남기고 None."""
        if self._db_path is None or self._backup_dir is None:
            return None
        db_path, backup_dir, now = self._db_path, self._backup_dir, self._clock()
        try:
            info = await asyncio.to_thread(create_backup, db_path, backup_dir, now)
            removed = await asyncio.to_thread(prune_backups, backup_dir, self._keep)
        except BackupError as exc:
            logger.error("Database backup failed: error={}", exc)
            return None
        except Exception:
            logger.opt(exception=True).error("Database backup crashed")
            return None
        logger.info(
            "Database backup completed: file={} size_bytes={} pruned={} keep={}",
            info.path.name,
            info.size_bytes,
            removed,
            self._keep,
        )
        return info

    def start(self) -> None:
        """백업 루프를 띄운다. 비활성이면 아무것도 하지 않는다."""
        if not self.active:
            reason = "disabled" if not self._enabled else "not a file database"
            logger.info("Database backup inactive: reason={}", reason)
            return
        self._task = asyncio.create_task(self._loop(), name="db-backup")

    async def stop(self) -> None:
        """진행 중인 백업은 끝까지 기다리고(스레드를 남기지 않는다) 루프를 멈춘다."""
        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(task, STOP_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Database backup did not stop in time")

    async def _wait(self, seconds: float) -> bool:
        """seconds 동안 기다린다. 그 사이 stop 이 불리면 True."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), max(seconds, 0.0))
        return self._stop.is_set()

    def _needs_catch_up(self) -> bool:
        latest = self.latest()
        return latest is None or self._clock() - latest.created_at > CATCH_UP_WINDOW

    async def _loop(self) -> None:
        if self._needs_catch_up():
            await self.run_backup()
        while not self._stop.is_set():
            now = self._clock()
            delay = (next_run_after(now, self._hour, self._tz) - now).total_seconds()
            if await self.waiter(delay):
                return
            await self.run_backup()
