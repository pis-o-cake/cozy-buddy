"""SQLite 백업 목록·복원 도구. 서버가 멈춘 상태에서만 복원한다.

절차:
  1. 서버를 멈춘다.      docker compose stop cozy-server
  2. 백업을 확인한다.    python scripts/restore_backup.py list
  3. 복원한다.           python scripts/restore_backup.py restore latest
                         (파일명이나 경로도 가능. 컨테이너가 멈춰 있으면
                          docker compose run --rm --no-deps cozy-server \\
                            python scripts/restore_backup.py restore latest)
  4. 서버를 시작한다.    docker compose start cozy-server

복원 전에 현재 DB 를 `<db>.pre-restore-<UTC 시각>` 으로 안전 복사한다. DB 가 다른 프로세스에
열려 있으면(서버 실행 중) 아무것도 바꾸지 않고 거부한다.
"""

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.core.maintenance import (  # noqa: E402
    TIMESTAMP_FORMAT,
    BackupError,
    BackupInfo,
    list_backups,
    resolve_backup_dir,
    resolve_db_path,
    verify_database,
)

EXIT_FAILURE = 1


def assert_db_unused(db_path: Path) -> None:
    """다른 연결이 DB 를 쓰고 있으면 BackupError. 배타 잠금을 잡을 수 있어야 통과한다."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path, timeout=0)
        try:
            conn.execute("PRAGMA locking_mode=EXCLUSIVE")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        raise BackupError(f"Database is in use (stop the server first): {exc}") from exc


def _copy(source_uri: str, target: Path, uri: bool = False) -> None:
    src = sqlite3.connect(source_uri, uri=uri, timeout=0)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def restore_backup(backup: Path, db_path: Path, now: datetime | None = None) -> Path | None:
    """backup 을 db_path 로 복원한다. 기존 DB 의 안전 복사본 경로(없었으면 None)를 돌려준다.

    Raises:
        BackupError: 백업이 없거나 손상됐거나 DB 가 사용 중일 때. 이 경우 DB 는 그대로다.
    """
    now = now or datetime.now(UTC)
    if not backup.is_file():
        raise BackupError(f"Backup not found: {backup}")
    verify_database(backup)
    assert_db_unused(db_path)
    safety: Path | None = None
    if db_path.exists():
        safety = db_path.with_name(f"{db_path.name}.pre-restore-{now.strftime(TIMESTAMP_FORMAT)}")
        _copy(str(db_path), safety)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp = db_path.with_name(f".{db_path.name}.restore.tmp")
    try:
        _copy(f"file:{backup}?mode=ro", temp, uri=True)
        # IMPORTANT: 이전 DB 의 -wal/-shm 이 새 파일에 붙으면 손상되므로 교체 전에 지운다.
        for suffix in ("-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        os.replace(temp, db_path)
    finally:
        temp.unlink(missing_ok=True)
    return safety


def _find_backup(spec: str, backups: list[BackupInfo]) -> Path:
    if spec == "latest":
        if not backups:
            raise BackupError("No backups found")
        return backups[0].path
    for info in backups:
        if info.path.name == spec:
            return info.path
    return Path(spec)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 성공하면 0, 실패하면 1."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", help="DB file (default: from COZY_DATABASE_URL)")
    parser.add_argument("--backup-dir", help="backup directory (default: from COZY_BACKUP_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list backups, newest first")
    restore = sub.add_parser("restore", help="restore a backup (server must be stopped)")
    restore.add_argument("backup", help="'latest', a file name in the backup dir, or a path")
    args = parser.parse_args(argv)

    settings = Settings()
    db_path = Path(args.db) if args.db else resolve_db_path(settings.database_url)
    if db_path is None:
        print("error: COZY_DATABASE_URL is not a SQLite file database", file=sys.stderr)
        return EXIT_FAILURE
    backup_dir = Path(args.backup_dir) if args.backup_dir else resolve_backup_dir(settings, db_path)
    backups = list_backups(backup_dir)

    if args.command == "list":
        for info in backups:
            print(f"{info.path.name}\t{info.size_bytes}\t{info.created_at.isoformat()}")
        return 0
    try:
        backup = _find_backup(args.backup, backups)
        safety = restore_backup(backup, db_path)
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"restored {backup} -> {db_path}")
    if safety is not None:
        print(f"previous database saved as {safety}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
