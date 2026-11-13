"""home 도메인 내부 모델. 프로토콜 메시지로의 변환은 mapping.py 가 맡는다."""

from pydantic import BaseModel, ConfigDict, Field

# 브리지 대상 도메인. 여기에 없는 엔티티는 노출 설정과 무관하게 클라이언트에 보내지 않는다.
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {
        "light",
        "switch",
        "fan",
        "cover",
        "climate",
        "media_player",
        "scene",
        "script",
        "sensor",
        "binary_sensor",
        "lock",
    }
)

# 클라이언트 표시에 쓰는 속성만 남긴다. 값은 문자열로 직렬화한다.
KEPT_ATTRIBUTES: tuple[str, ...] = (
    "friendly_name",
    "brightness",
    "color_temp_kelvin",
    "unit_of_measurement",
    "device_class",
)


class HomeArea(BaseModel):
    """HA area(방)."""

    model_config = ConfigDict(frozen=True)

    area_id: str
    name: str


class HomeEntity(BaseModel):
    """클라이언트에 노출하는 엔티티와 현재 상태."""

    model_config = ConfigDict(frozen=True)

    entity_id: str
    area_id: str = ""
    name: str
    domain: str
    state: str
    attributes: dict[str, str] = Field(default_factory=dict)
    changed_epoch_ms: int = 0


class HomeCatalog(BaseModel):
    """한 번의 (재)연결에서 만든 area/엔티티 캐시."""

    areas: dict[str, HomeArea] = Field(default_factory=dict)
    entities: dict[str, HomeEntity] = Field(default_factory=dict)


class ServiceCall(BaseModel):
    """HA `call_service` 한 건."""

    model_config = ConfigDict(frozen=True)

    domain: str
    service: str
    data: dict[str, int | str] = Field(default_factory=dict)
