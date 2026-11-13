"""알람 REST 스키마."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlarmRead(BaseModel):
    """알람 사본 조회 응답."""

    model_config = ConfigDict(from_attributes=True)

    hub_id: str
    alarm_id: str
    kind: str
    label: str
    fires_at_epoch_ms: int | None
    hour: int | None
    minute: int | None
    days: list[int]
    enabled: bool
    sunrise: bool
    revision: int
    sync_state: str
    updated_at: datetime
