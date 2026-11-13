"""SQLAlchemy 비동기 엔진·세션과 선언 베이스."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import DateTime, TypeDecorator, event
from sqlalchemy.engine import Dialect, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from starlette.requests import HTTPConnection


class Base(DeclarativeBase):
    """모든 도메인 모델의 선언 베이스."""


class UTCDateTime(TypeDecorator[datetime]):
    """UTC 로 저장하고 tz-aware UTC 로 돌려주는 시각 타입. SQLite 는 tz 를 보존하지 않는다."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    """현재 시각(UTC, tz-aware)."""
    return datetime.now(UTC)


def ensure_sqlite_dir(url: str) -> None:
    """SQLite 파일 경로의 상위 디렉터리를 만든다."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        return
    if parsed.database == ":memory:":
        return
    Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
    """엔진과 세션 팩토리를 묶는다. 앱 상태(app.state.db)에 하나만 둔다."""

    def __init__(self, url: str) -> None:
        ensure_sqlite_dir(url)
        self.engine: AsyncEngine = create_async_engine(url)
        if make_url(url).drivername.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", _set_sqlite_pragmas)
        self._factory = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        """새 세션을 만든다. 호출자가 `async with` 로 닫는다."""
        return self._factory()

    async def dispose(self) -> None:
        """커넥션 풀을 정리한다."""
        await self.engine.dispose()


def get_database(conn: HTTPConnection) -> Database:
    """HTTP/WebSocket 공용 Database 의존성."""
    db: Database = conn.app.state.db
    return db


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """요청 범위 세션 의존성."""
    async with get_database(request).session() as session:
        yield session
