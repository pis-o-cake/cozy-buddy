"""애플리케이션 설정. 환경변수(COZY_ 접두사)와 .env 에서 읽는다."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientRole(StrEnum):
    """연결 클라이언트 종류."""

    HUB = "hub"
    MOBILE = "mobile"


class TokenEntry(BaseModel, frozen=True):
    """정적 토큰 한 건."""

    kind: ClientRole
    client_id: str
    token: SecretStr


def parse_static_tokens(raw: str) -> tuple[TokenEntry, ...]:
    """`kind:client_id:token` 쉼표 목록을 파싱한다.

    Args:
        raw: 환경변수 원문. 빈 문자열이면 토큰이 없는 것으로 본다.

    Returns:
        파싱된 토큰 항목.

    Raises:
        ValueError: 형식이 틀리거나 kind 가 hub/mobile 이 아닐 때.
    """
    entries: list[TokenEntry] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        if len(parts) != 3 or not all(parts):
            raise ValueError("Static token entry must be 'kind:client_id:token'")
        entries.append(TokenEntry(kind=ClientRole(parts[0]), client_id=parts[1], token=parts[2]))
    return tuple(entries)


class Settings(BaseSettings):
    """서버 설정."""

    # IMPORTANT: 저장소 루트 .env 에는 다른 컴포넌트의 키도 있으므로 extra 는 무시한다.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="COZY_",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///data/cozy.db"
    static_tokens: SecretStr = SecretStr("")
    hello_timeout_seconds: float = 10.0
    log_level: str = "INFO"
    locale: str = "ko"
    # 둘 중 하나라도 비어 있으면 HA 브리지는 꺼진다.
    ha_url: str = ""
    ha_token: SecretStr = SecretStr("")
    ha_request_timeout_seconds: float = 5.0
    ha_backoff_min_seconds: float = 1.0
    ha_backoff_max_seconds: float = 30.0

    @model_validator(mode="after")
    def _validate_tokens(self) -> Self:
        parse_static_tokens(self.static_tokens.get_secret_value())
        return self

    @property
    def token_entries(self) -> tuple[TokenEntry, ...]:
        """파싱된 정적 토큰 목록."""
        return parse_static_tokens(self.static_tokens.get_secret_value())

    @property
    def ha_enabled(self) -> bool:
        """HA 브리지를 켤 수 있는지(URL 과 토큰이 모두 있는지)."""
        return bool(self.ha_url.strip()) and bool(self.ha_token.get_secret_value().strip())
