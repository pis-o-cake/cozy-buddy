"""HomeSnapshot 에서 매처 입력(HomeModel)과 현재 상태를 한 번에 만든다."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from cozy.v1.home_pb2 import HomeSnapshot

from app.domain.agent.model import AreaRef, EntityRef, HomeModel

_HA_BRIGHTNESS_MAX = 255
_STATE_OFF = "off"
STT_PROMPT_MAX_CHARS = 600


@dataclass(frozen=True, slots=True)
class EntityStatus:
    """엔티티의 현재 상태 문자열과 속성."""

    state: str
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HomeView:
    """한 요청이 보는 집의 구조와 상태. 요청 시작 시점의 캐시 사본이다."""

    model: HomeModel
    status: Mapping[str, EntityStatus]

    def brightness_pct(self, entity_id: str) -> int:
        """현재 밝기(0~100). 꺼져 있으면 0, 켜졌는데 밝기 속성이 없으면 100."""
        current = self.status.get(entity_id)
        if current is None or current.state == _STATE_OFF:
            return 0
        raw = current.attributes.get("brightness", "")
        if not raw.isdecimal():
            return 100
        return round(int(raw) * 100 / _HA_BRIGHTNESS_MAX)

    def entity(self, entity_id: str) -> EntityRef | None:
        """entity_id 로 엔티티 참조를 찾는다."""
        return next((e for e in self.model.entities if e.entity_id == entity_id), None)

    def area_name(self, area_id: str | None) -> str:
        """방 이름. 모르면 빈 문자열."""
        return next((a.name for a in self.model.areas if a.area_id == area_id), "")


def build_home_view(snapshot: HomeSnapshot) -> HomeView:
    """스냅샷을 HomeView 로 바꾼다.

    Note:
        스냅샷에는 별칭 정보가 없어 aliases 는 비어 있다.
    """
    areas = [AreaRef(area_id=a.area_id, name=a.name) for a in snapshot.areas]
    entities = [
        EntityRef(
            entity_id=e.entity_id,
            area_id=e.area_id or None,
            name=e.name,
            domain=e.domain,
        )
        for e in snapshot.entities
    ]
    status = {
        e.entity_id: EntityStatus(e.state.state, dict(e.state.attributes))
        for e in snapshot.entities
    }
    return HomeView(HomeModel(areas=areas, entities=entities), status)


def home_names(model: HomeModel) -> list[str]:
    """STT 힌트로 쓸 방·기기 이름. 중복을 없애고 길이를 제한한다."""
    names: dict[str, None] = {}
    total = 0
    for name in [*(a.name for a in model.areas), *(e.name for e in model.entities)]:
        if not name or name in names:
            continue
        total += len(name) + 2
        if total > STT_PROMPT_MAX_CHARS:
            break
        names[name] = None
    return list(names)
