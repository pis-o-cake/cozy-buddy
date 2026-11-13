"""Alembic 비동기 환경."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.core.database import Base, ensure_sqlite_dir
from app.core.discovery import discover_domain_modules

# 모든 도메인 모델을 import 해 메타데이터에 등록한다.
discover_domain_modules("models")

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().database_url


def run_migrations_offline() -> None:
    """DB 연결 없이 SQL 만 출력한다."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    # SQLite 는 ALTER 가 제한적이라 batch 모드로 렌더링한다.
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """비동기 엔진으로 마이그레이션을 실행한다."""
    url = _database_url()
    ensure_sqlite_dir(url)
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
