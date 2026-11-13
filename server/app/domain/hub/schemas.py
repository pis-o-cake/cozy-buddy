"""허브 REST 스키마."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HubRead(BaseModel):
    """허브 조회 응답."""

    model_config = ConfigDict(from_attributes=True)

    hub_id: str
    name: str
    area_id: str
    last_seen_at: datetime | None
    created_at: datetime
    online: bool = False


class HubUpdate(BaseModel):
    """허브 수정 요청. 보낸 필드만 바꾼다."""

    name: str | None = None
    area_id: str | None = None
