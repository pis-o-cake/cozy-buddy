"""방송 REST 스키마."""

from pydantic import BaseModel, Field

from app.domain.broadcast.service import ALL_TARGET, MAX_TEXT_CHARS


class BroadcastBody(BaseModel):
    """방송 요청. target 은 방·허브 이름이며 "all" 이면 전체 허브다."""

    target: str = Field(default=ALL_TARGET, min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    from_hub_id: str = Field(default="", max_length=64)


class BroadcastResult(BaseModel):
    """방송 결과. offline 은 대상이지만 받지 못한 허브다."""

    target: str
    delivered: list[str]
    offline: list[str]
    audio: bool
