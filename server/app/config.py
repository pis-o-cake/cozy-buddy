"""애플리케이션 설정. 환경변수(COZY_ 접두사)와 .env 에서 읽는다."""

from enum import StrEnum
from typing import Self

from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator, model_validator
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
    # 관리 API(/api/auth 의 코드 발급·폐기) 보호용. 비어 있으면 관리 API 는 503 을 돌려준다.
    admin_token: SecretStr = SecretStr("")
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
    # 첫 STT 요청이 이 시간 안에 끝나지 않으면 같은 요청을 한 번 더 보낸다(헤지). 0 이하면 끈다.
    # 정상 STT p95 는 약 1.1~1.8 s 이고 스파이크는 10 s 이상이라 1800 ms 로 둔다.
    stt_hedge_after_ms: int = 1800
    # STT 전체 제한 시간. 넘기면 stt_timeout 으로 실패 처리한다.
    stt_timeout_ms: int = Field(default=6000, ge=1)
    # 결정론 템플릿 응답의 합성 음성 캐시. 같은 문장은 OpenAI TTS 를 다시 부르지 않는다.
    tts_cache_enabled: bool = True
    tts_cache_dir: str = "data/tts-cache"
    tts_cache_memory_mb: int = Field(default=64, ge=1)
    tts_cache_disk_mb: int = Field(default=256, ge=0)
    # 켜면 LLM 이 만든 자유 문장도 캐시한다. 거의 재사용되지 않아 기본은 끈다.
    tts_cache_llm: bool = False
    # 자주 쓰는 확인 문장(기기 켜기·끄기 등)을 미리 합성한다. 하루 합성 문장 수에 상한이 있다.
    tts_warmup: bool = True
    tts_warmup_max_per_day: int = Field(default=200, ge=0)
    tts_warmup_poll_seconds: float = Field(default=30.0, gt=0)
    llm_timeout_seconds: float = 10.0
    max_utterance_seconds: float = 15.0
    clarify_ttl_seconds: float = 30.0
    # 직전 턴 대상에 대한 후속 지시를 받는 시간과 LLM 에 넘기는 최근 대화 보존 시간.
    followup_ttl_seconds: float = 60.0
    history_ttl_seconds: float = 300.0
    timezone: str = "Asia/Seoul"
    # 턴 로그(발화별 경로·지연 기록) 보존 기간. 시작할 때와 하루마다 이보다 오래된 행을 지운다.
    turn_log_days: int = Field(default=30, ge=1)
    # 집 좌표. 둘 중 하나라도 비어 있으면 날씨 기능은 꺼진다.
    home_latitude: float | None = Field(default=None, ge=-90, le=90)
    home_longitude: float | None = Field(default=None, ge=-180, le=180)
    weather_base_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_poll_seconds: float = 1800.0
    # 이 시간보다 오래된 캐시는 응답과 푸시에 쓰지 않는다.
    weather_stale_seconds: float = 3 * 3600.0
    weather_timeout_seconds: float = 8.0
    weather_max_attempts: int = 3
    weather_backoff_seconds: float = 1.0

    @field_validator("home_latitude", "home_longitude", mode="before")
    @classmethod
    def _blank_coordinate_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

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
    def weather_enabled(self) -> bool:
        """날씨 조회를 켤 수 있는지(집 위도와 경도가 모두 있는지)."""
        return self.home_latitude is not None and self.home_longitude is not None

    @property
    def ha_enabled(self) -> bool:
        """HA 브리지를 켤 수 있는지(URL 과 토큰이 모두 있는지)."""
        return bool(self.ha_url.strip()) and bool(self.ha_token.get_secret_value().strip())
