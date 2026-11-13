"""알람 사본 모델."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime, utcnow


class SyncState(StrEnum):
    """허브와의 동기화 상태."""

    SYNCED = "synced"
    PENDING_TO_HUB = "pending_to_hub"
    PENDING_DELETE = "pending_delete"


class AlarmKindName(StrEnum):
    """cozy.v1.AlarmKind 의 저장용 이름."""

    ALARM = "alarm"
    TIMER = "timer"


class Alarm(Base):
    """cozy.v1.Alarm 의 서버 사본. 정본은 허브다.

    일정은 직렬화하지 않고 컬럼으로 푼다. 일회성이면 fires_at_epoch_ms,
    반복이면 hour/minute/days 를 채운다.
    """

    __tablename__ = "alarm"

    hub_id: Mapped[str] = mapped_column(ForeignKey("hub.hub_id"), primary_key=True)
    alarm_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default=AlarmKindName.ALARM)
    label: Mapped[str] = mapped_column(String(200), default="")
    fires_at_epoch_ms: Mapped[int | None] = mapped_column(BigInteger, default=None)
    # 허브 로컬 벽시계 시각
    hour: Mapped[int | None] = mapped_column(Integer, default=None)
    minute: Mapped[int | None] = mapped_column(Integer, default=None)
    # cozy.v1.DayOfWeek 값(1=월 ... 7=일)의 JSON 배열
    days: Mapped[list[int]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sunrise: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    sync_state: Mapped[str] = mapped_column(String(32), default=SyncState.SYNCED)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
