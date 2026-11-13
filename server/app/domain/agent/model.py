"""매처 입력 스냅샷과 결과 타입. home 도메인에 의존하지 않는다."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# 상대 밝기 명령("밝게"/"어둡게")의 변화량(%p). 실행 쪽이 현재 밝기에 더해 1~100 으로 자른다.
RELATIVE_BRIGHTNESS_STEP = 30


@dataclass(frozen=True, slots=True)
class AreaRef:
    """방(HA area) 참조."""

    area_id: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntityRef:
    """엔티티 참조. domain 은 HA 도메인(light, switch, fan, cover, climate, scene, sensor)."""

    entity_id: str
    area_id: str | None
    name: str
    domain: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HomeModel:
    """매처가 보는 집 구조 스냅샷."""

    areas: list[AreaRef] = field(default_factory=list)
    entities: list[EntityRef] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UtteranceContext:
    """발화 한 건의 문맥.

    Attributes:
        text: STT 결과 원문.
        hub_area_id: 발화한 허브가 놓인 방. 모르면 None.
        now: 시간대가 있는 현재 시각. 알람 시각 해석의 기준이다.
    """

    text: str
    hub_area_id: str | None
    now: datetime

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            raise ValueError("now must be timezone-aware")


class DeviceAction(StrEnum):
    """기기 제어 동작. cover 는 turn_on=열기, turn_off=닫기로 쓴다."""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    TOGGLE = "toggle"
    SET_BRIGHTNESS = "set_brightness"


class ClarifyReason(StrEnum):
    """확인 질문이 필요한 경우."""

    AMBIGUOUS_TARGET = "ambiguous_target"
    BROAD_ACTION = "broad_action"


@dataclass(frozen=True, slots=True)
class CommandParams:
    """동작 인자. 밝기는 절대값(brightness_pct)과 상대값(brightness_delta_pct) 중 하나만 채운다."""

    brightness_pct: int | None = None
    brightness_delta_pct: int | None = None


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    """기기 제어."""

    entity_ids: tuple[str, ...]
    action: DeviceAction
    params: CommandParams = field(default_factory=CommandParams)


@dataclass(frozen=True, slots=True)
class SceneCommand:
    """장면 실행."""

    entity_id: str


@dataclass(frozen=True, slots=True)
class StateQuery:
    """상태 질의. 어떤 상태를 물었는지는 담지 않고 응답 단계가 현재 상태로 답한다."""

    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AlarmIntent:
    """알람 생성. days(ISO 요일 1=월..7=일) 와 one_shot_epoch_ms 중 정확히 하나를 채운다."""

    hour: int
    minute: int
    days: tuple[int, ...] | None = None
    one_shot_epoch_ms: int | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class TimerIntent:
    """타이머 생성."""

    duration_seconds: int
    label: str | None = None


@dataclass(frozen=True, slots=True)
class CancelAlarmIntent:
    """알람/타이머 취소.

    label·시각이 모두 없으면 "가장 가까운 것/하나뿐인 것"을 뜻하며, 여럿이면 실행 쪽이 되묻는다.
    hour_ambiguous 가 True 면 hour 는 말한 그대로(1~12)이고 오전/오후 둘 다 후보다.
    """

    label: str | None = None
    hour: int | None = None
    minute: int | None = None
    hour_ambiguous: bool = False
    is_timer: bool = False


@dataclass(frozen=True, slots=True)
class Clarify:
    """확인 질문. action/params 는 사용자가 확정하면 그대로 실행할 보류 명령이다."""

    reason: ClarifyReason
    candidate_entity_ids: tuple[str, ...] = ()
    action: DeviceAction | None = None
    params: CommandParams | None = None


@dataclass(frozen=True, slots=True)
class NoMatch:
    """확신 있게 해석하지 못함. 이후 LLM 이 처리한다."""


MatchResult = (
    DeviceCommand
    | SceneCommand
    | StateQuery
    | AlarmIntent
    | TimerIntent
    | CancelAlarmIntent
    | Clarify
    | NoMatch
)
