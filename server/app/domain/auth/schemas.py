"""인증 REST 스키마."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.config import ClientRole


class PairingCodeCreate(BaseModel):
    """등록 코드 발급 요청. 이름과 방은 허브 등록 시 반영된다. 발급될 종류는 관리자가 정한다."""

    name: str = Field(default="", max_length=100)
    area_id: str = Field(default="", max_length=100)
    client_kind: ClientRole = ClientRole.HUB


class PairingCodeRead(BaseModel):
    """발급된 등록 코드."""

    code: str
    expires_in_seconds: int


class PairRequest(BaseModel):
    """코드로 등록하는 요청. 종류는 코드에 묶여 있어 하위 호환용으로만 받고, 다르면 거절한다."""

    code: str = Field(min_length=1, max_length=16)
    client_kind: ClientRole | None = None


class PairResponse(BaseModel):
    """발급된 자격 증명. 토큰은 이 응답에서만 볼 수 있다."""

    client_id: str
    token: str


class ClientRead(BaseModel):
    """발급된 클라이언트 요약. 토큰·해시는 담지 않는다."""

    client_id: str
    kind: ClientRole
    name: str | None = None
    area_id: str | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    connected: bool
