"""턴 로그 테이블."""

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime, utcnow


class TurnLog(Base):
    """끝난 턴 한 건. 오디오는 저장하지 않는다. hub 외래키를 두지 않아 미등록 허브도 남는다."""

    __tablename__ = "turn_log"
    __table_args__ = (Index("ix_turn_log_hub_created", "hub_id", "created_at"),)

    turn_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    hub_id: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(8))
    transcript: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str] = mapped_column(String(16))
    intent: Mapped[str] = mapped_column(String(64), default="")
    skill_result: Mapped[str] = mapped_column(String(8), default="none")
    response_text: Mapped[str] = mapped_column(Text, default="")
    t_stt_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    t_understand_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    t_act_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    t_first_audio_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    llm_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    stt_hedged: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    error_code: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
