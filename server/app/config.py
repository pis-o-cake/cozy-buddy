"""애플리케이션 설정. 환경변수(COZY_ 접두사)와 .env 에서 읽는다."""

from enum import StrEnum
from typing import Self

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
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
        populate_by_name=True,
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
    # 레지스트리 이벤트가 몰릴 때 캐시 재구성을 한 번으로 묶는 대기 시간.
    ha_registry_debounce_seconds: float = 1.0
    # 이벤트가 없는 Assist 노출 변경을 잡는 폴링 주기. 0 이하면 폴링하지 않는다.
    ha_expose_poll_seconds: float = 60.0

    # IMPORTANT: OpenAI 키·모델은 저장소 루트 .env 와 공유하는 비접두사 이름(OPENAI_*)을 쓴다.
    # 키가 있으면 음성 파이프라인이 켜지고, 이때 모델 이름은 코드 기본값 없이 모두 필수다(ADR 0004).
    openai_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("OPENAI_API_KEY")
    )
    openai_stt_model: str = Field(default="", validation_alias=AliasChoices("OPENAI_STT_MODEL"))
    openai_llm_model: str = Field(default="", validation_alias=AliasChoices("OPENAI_LLM_MODEL"))
    openai_tts_model: str = Field(default="", validation_alias=AliasChoices("OPENAI_TTS_MODEL"))
    openai_tts_voice: str = Field(default="", validation_alias=AliasChoices("OPENAI_TTS_VOICE"))
    # 추론 모델이 아니면 비워 파라미터를 보내지 않는다.
    openai_llm_reasoning_effort: str = Field(
        default="none", validation_alias=AliasChoices("OPENAI_LLM_REASONING_EFFORT")
    )
    # STT prompt 에 집의 방·기기 이름을 넣어 고유명사 오인식을 줄인다(S2b 에서 효과 측정 중).
    stt_prompt_enabled: bool = True
    llm_timeout_seconds: float = 10.0
    max_utterance_seconds: float = 15.0
    clarify_ttl_seconds: float = 30.0
    timezone: str = "Asia/Seoul"

    @model_validator(mode="after")
    def _validate_tokens(self) -> Self:
        parse_static_tokens(self.static_tokens.get_secret_value())
        return self

    @model_validator(mode="after")
    def _validate_voice(self) -> Self:
        if not self.voice_enabled:
            return self
        missing = [
            name
            for name, value in (
                ("OPENAI_STT_MODEL", self.openai_stt_model),
                ("OPENAI_LLM_MODEL", self.openai_llm_model),
                ("OPENAI_TTS_MODEL", self.openai_tts_model),
                ("OPENAI_TTS_VOICE", self.openai_tts_voice),
            )
            if not value.strip()
        ]
        if missing:
            raise ValueError(f"Voice pipeline requires settings: {', '.join(missing)}")
        return self

    @property
    def voice_enabled(self) -> bool:
        """음성 파이프라인을 켤 수 있는지(OPENAI_API_KEY 가 있는지)."""
        return bool(self.openai_api_key.get_secret_value().strip())

    @property
    def token_entries(self) -> tuple[TokenEntry, ...]:
        """파싱된 정적 토큰 목록."""
        return parse_static_tokens(self.static_tokens.get_secret_value())

    @property
    def ha_enabled(self) -> bool:
        """HA 브리지를 켤 수 있는지(URL 과 토큰이 모두 있는지)."""
        return bool(self.ha_url.strip()) and bool(self.ha_token.get_secret_value().strip())
