"""허브 모델."""

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime, utcnow


class Hub(Base):
    """등록된 허브. 첫 hello 성공 시 자동 생성된다."""

    __tablename__ = "hub"

    hub_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    # HA area ID
    area_id: Mapped[str] = mapped_column(String(100), default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
