"""SQLite 일일 백업·보존·복원."""

import asyncio
import importlib.util
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.core.maintenance import (
    BackupError,
    BackupScheduler,
    create_backup,
    list_backups,
    next_run_after,
    prune_backups,
    resolve_db_path,
)
from app.main import create_app
from tests.conftest import ADMIN_TOKEN, SERVER_DIR, migrate

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
SEOUL = ZoneInfo("Asia/Seoul")


def _make_db(path: Path, rows: int = 3) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()
    return path


def _count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT count(*) FROM t").fetchone()[0])
    finally:
        conn.close()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'cozy.db'}",
        "backup_dir": str(tmp_path / "bk"),
        "timezone": "Asia/Seoul",
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[call-arg,arg-type]


@pytest.fixture
def restore_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "restore_backup", SERVER_DIR / "scripts" / "restore_backup.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_is_valid_single_file_with_utc_name(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "cozy.db")
    info = create_backup(db, tmp_path / "bk", NOW)
    assert info.path.name == "cozy-20260501T120000Z.db"
    assert _count(info.path) == 3
    assert sorted(p.name for p in (tmp_path / "bk").iterdir()) == [info.path.name]


def test_backup_while_writer_holds_wal_connection(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "cozy.db")
    writer = sqlite3.connect(db)
    writer.execute("INSERT INTO t VALUES (99)")
    writer.commit()
    try:
        info = create_backup(db, tmp_path / "bk", NOW)
    finally:
        writer.close()
    assert _count(info.path) == 4


def test_missing_database_fails_without_leftovers(tmp_path: Path) -> None:
    with pytest.raises(BackupError):
        create_backup(tmp_path / "nope.db", tmp_path / "bk", NOW)
    assert not list((tmp_path / "bk").glob("*"))


def test_failed_integrity_check_removes_temp_and_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _make_db(tmp_path / "cozy.db")

    def broken(_path: Path) -> None:
        raise BackupError("bad")

    monkeypatch.setattr("app.core.maintenance.verify_database", broken)
    with pytest.raises(BackupError):
        create_backup(db, tmp_path / "bk", NOW)
    assert list((tmp_path / "bk").iterdir()) == []


def test_prune_keeps_newest_and_removes_stale_temp(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "cozy.db")
    bk = tmp_path / "bk"
    for day in range(5):
        create_backup(db, bk, NOW + timedelta(days=day))
    stale = bk / ".cozy-20260101T000000Z.db.tmp"
    stale.write_bytes(b"x")
    (bk / "notes.txt").write_text("keep me")

    assert prune_backups(bk, 2) == 3

    names = [b.path.name for b in list_backups(bk)]
    assert names == ["cozy-20260505T120000Z.db", "cozy-20260504T120000Z.db"]
    assert not stale.exists()
    assert (bk / "notes.txt").exists()


@pytest.mark.parametrize(
    "url",
    ["sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://", "postgresql+asyncpg://u@h/db"],
)
def test_non_file_databases_are_skipped(tmp_path: Path, url: str) -> None:
    assert resolve_db_path(url) is None
    scheduler = BackupScheduler(_settings(tmp_path, database_url=url))
    assert not scheduler.active
    scheduler.start()
    assert scheduler.latest() is None


async def test_disabled_scheduler_does_nothing(tmp_path: Path) -> None:
    _make_db(tmp_path / "cozy.db")
    scheduler = BackupScheduler(_settings(tmp_path, backup_enabled=False), lambda: NOW)
    scheduler.start()
    await scheduler.stop()
    assert scheduler.latest() is None
    assert not (tmp_path / "bk").exists()


def test_default_backup_dir_is_next_to_database(tmp_path: Path) -> None:
    settings = _settings(tmp_path, backup_dir="")
    scheduler = BackupScheduler(settings, lambda: NOW)
    _make_db(tmp_path / "cozy.db")
    assert scheduler._backup_dir == tmp_path / "backups"  # noqa: SLF001


@pytest.mark.parametrize(
    ("now", "hour", "expected_utc"),
    [
        # 서울 04:00 = 전날 UTC 19:00. 서울 09:00 이면 다음 04:00 은 이튿날
        (datetime(2026, 5, 1, 0, 0, tzinfo=UTC), 4, datetime(2026, 5, 1, 19, 0, tzinfo=UTC)),
        (datetime(2026, 4, 30, 18, 0, tzinfo=UTC), 4, datetime(2026, 4, 30, 19, 0, tzinfo=UTC)),
        # 정각이면 다음 날
        (datetime(2026, 4, 30, 19, 0, tzinfo=UTC), 4, datetime(2026, 5, 1, 19, 0, tzinfo=UTC)),
        # 자정을 넘기는 경우: 현지 23:30 -> 다음 날 00:00
        (datetime(2026, 4, 30, 14, 30, tzinfo=UTC), 0, datetime(2026, 4, 30, 15, 0, tzinfo=UTC)),
    ],
)
def test_next_run_across_midnight_and_timezone(
    now: datetime, hour: int, expected_utc: datetime
) -> None:
    assert next_run_after(now, hour, SEOUL) == expected_utc


def test_next_run_follows_dst_offsets() -> None:
    ny = ZoneInfo("America/New_York")
    # 2026-03-08 02:00 EST 에 서머타임 시작: 04:00 은 EDT(UTC-4) -> 08:00Z
    before = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    assert next_run_after(before, 4, ny) == datetime(2026, 3, 8, 8, 0, tzinfo=UTC)
    # 전날 04:00 EST(UTC-5) -> 09:00Z
    assert next_run_after(datetime(2026, 3, 7, 5, 0, tzinfo=UTC), 4, ny) == datetime(
        2026, 3, 7, 9, 0, tzinfo=UTC
    )


async def test_startup_catch_up_runs_when_no_recent_backup(tmp_path: Path) -> None:
    _make_db(tmp_path / "cozy.db")
    scheduler = BackupScheduler(_settings(tmp_path), lambda: NOW)

    async def stop_at_once(_seconds: float) -> bool:
        return True

    scheduler.waiter = stop_at_once
    scheduler.start()
    await scheduler.stop()

    latest = scheduler.latest()
    assert latest is not None and latest.created_at == NOW


async def test_startup_skips_catch_up_when_recent_backup_exists(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "cozy.db")
    create_backup(db, tmp_path / "bk", NOW - timedelta(hours=23))
    scheduler = BackupScheduler(_settings(tmp_path), lambda: NOW)

    async def stop_at_once(_seconds: float) -> bool:
        return True

    scheduler.waiter = stop_at_once
    scheduler.start()
    await scheduler.stop()

    assert len(list_backups(tmp_path / "bk")) == 1


async def test_startup_catches_up_when_backup_is_older_than_24h(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "cozy.db")
    create_backup(db, tmp_path / "bk", NOW - timedelta(hours=25))
    scheduler = BackupScheduler(_settings(tmp_path), lambda: NOW)

    async def stop_at_once(_seconds: float) -> bool:
        return True

    scheduler.waiter = stop_at_once
    scheduler.start()
    await scheduler.stop()

    assert len(list_backups(tmp_path / "bk")) == 2


async def test_loop_waits_until_scheduled_hour_then_backs_up(tmp_path: Path) -> None:
    _make_db(tmp_path / "cozy.db")
    # 서울 03:00 에 시작하면 다음 실행은 04:00 -> 1시간 뒤
    clock_now = [datetime(2026, 5, 1, 18, 0, tzinfo=UTC)]
    scheduler = BackupScheduler(_settings(tmp_path, backup_keep=2), lambda: clock_now[0])
    delays: list[float] = []
    finished = asyncio.Event()

    async def fake_wait(seconds: float) -> bool:
        delays.append(seconds)
        if len(delays) > 2:
            finished.set()
            return True
        clock_now[0] += timedelta(seconds=seconds)
        return False

    scheduler.waiter = fake_wait
    scheduler.start()
    await asyncio.wait_for(finished.wait(), 5)
    await scheduler.stop()

    assert delays == [3600.0, 24 * 3600.0, 24 * 3600.0]
    names = [b.path.name for b in list_backups(tmp_path / "bk")]
    assert names == ["cozy-20260502T190000Z.db", "cozy-20260501T190000Z.db"]


async def test_failed_backup_does_not_kill_the_loop(tmp_path: Path) -> None:
    scheduler = BackupScheduler(_settings(tmp_path), lambda: NOW)
    assert await scheduler.run_backup() is None


async def test_stop_leaves_no_backup_thread(tmp_path: Path) -> None:
    _make_db(tmp_path / "cozy.db")
    before = threading.active_count()
    scheduler = BackupScheduler(_settings(tmp_path), lambda: NOW)
    scheduler.start()
    await scheduler.stop()
    assert list_backups(tmp_path / "bk")
    assert threading.active_count() <= before + 1  # asyncio 기본 executor 스레드 하나


@pytest.fixture
def backup_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = _settings(
        tmp_path,
        backup_enabled=True,
        tts_cache_enabled=False,
        tts_cache_dir=str(tmp_path / "tts"),
        tts_warmup=False,
    )
    migrate(settings.database_url)
    secured = settings.model_copy(update={"admin_token": SecretStr(ADMIN_TOKEN)})
    with TestClient(create_app(secured)) as c:
        yield c


def test_app_runs_catch_up_backup_and_reports_status(
    backup_client: TestClient, tmp_path: Path
) -> None:
    def status() -> dict[str, object]:
        return dict(backup_client.get("/api/system/backup").json())

    from tests.conftest import eventually

    eventually(lambda: status()["last_backup_at"] is not None)
    body = status()
    assert body["enabled"] is True
    assert isinstance(body["last_backup_size_bytes"], int) and body["last_backup_size_bytes"] > 0
    assert len(list_backups(tmp_path / "bk")) == 1


def test_status_when_disabled(client: TestClient) -> None:
    body = client.get("/api/system/backup").json()
    assert body == {"enabled": False, "last_backup_at": None, "last_backup_size_bytes": None}


def test_restore_replaces_database_and_keeps_safety_copy(
    tmp_path: Path, restore_module: ModuleType
) -> None:
    db = _make_db(tmp_path / "cozy.db", rows=3)
    backup = create_backup(db, tmp_path / "bk", NOW).path
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO t VALUES (100)")
    conn.commit()
    conn.close()

    safety = restore_module.restore_backup(backup, db, NOW)

    assert _count(db) == 3
    assert safety is not None and _count(safety) == 4
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()


def test_restore_refuses_while_database_is_in_use(
    tmp_path: Path, restore_module: ModuleType
) -> None:
    db = _make_db(tmp_path / "cozy.db", rows=3)
    backup = create_backup(db, tmp_path / "bk", NOW).path
    holder = sqlite3.connect(db)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("SELECT count(*) FROM t").fetchall()
    holder.execute("INSERT INTO t VALUES (7)")
    holder.commit()
    holder.execute("SELECT count(*) FROM t").fetchall()
    try:
        with pytest.raises(BackupError, match="in use"):
            restore_module.restore_backup(backup, db, NOW)
        assert _count(db) == 4
        assert not list(tmp_path.glob("cozy.db.pre-restore-*"))
    finally:
        holder.close()


def test_restore_rejects_corrupt_backup(tmp_path: Path, restore_module: ModuleType) -> None:
    db = _make_db(tmp_path / "cozy.db")
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database" * 100)
    with pytest.raises(BackupError):
        restore_module.restore_backup(bad, db, NOW)
    assert _count(db) == 3


def test_restore_into_missing_database(tmp_path: Path, restore_module: ModuleType) -> None:
    backup = create_backup(_make_db(tmp_path / "src.db"), tmp_path / "bk", NOW).path
    target = tmp_path / "new" / "cozy.db"
    assert restore_module.restore_backup(backup, target, NOW) is None
    assert _count(target) == 3


def test_cli_list_and_restore_latest(
    tmp_path: Path,
    restore_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    db = _make_db(tmp_path / "cozy.db")
    bk = tmp_path / "bk"
    create_backup(db, bk, NOW)
    base = ["--db", str(db), "--backup-dir", str(bk)]
    run: Callable[[list[str]], int] = restore_module.main

    assert run([*base, "list"]) == 0
    assert "cozy-20260501T120000Z.db" in capsys.readouterr().out
    assert run([*base, "restore", "latest"]) == 0
    assert run([*base, "restore", "missing.db"]) == 1
