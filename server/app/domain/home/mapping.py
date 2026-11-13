"""HA 원본 데이터 → 캐시 모델 → protobuf 변환(순수 함수)."""

from datetime import datetime
from typing import Any

from cozy.v1.home_pb2 import Area, Entity, EntityState, EntityStateChanged, HomeSnapshot

from app.domain.home.schemas import (
    ALLOWED_DOMAINS,
    KEPT_ATTRIBUTES,
    HomeArea,
    HomeCatalog,
    HomeEntity,
)

# HA 의 "Assist 에 노출" 플래그 키.
ASSIST_ASSISTANT = "conversation"


def parse_exposed(result: Any) -> set[str]:
    """`homeassistant/expose_entity/list` 결과에서 Assist 에 노출된 entity_id 를 뽑는다."""
    exposed = (result or {}).get("exposed_entities") or {}
    return {
        entity_id
        for entity_id, flags in exposed.items()
        if isinstance(flags, dict) and flags.get(ASSIST_ASSISTANT)
    }


def parse_epoch_ms(value: Any) -> int:
    """HA ISO-8601 시각을 epoch ms 로. 파싱할 수 없으면 0."""
    if not isinstance(value, str):
        return 0
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return 0


def clean_aliases(raw: Any) -> tuple[str, ...]:
    """HA 별칭 목록에서 문자열만 남겨 공백 제거·중복 제거·정렬한다.

    HA 는 "엔티티 이름 사용" 자리표시자로 null 을 넣기도 한다.
    """
    if not isinstance(raw, list | tuple | set):
        return ()
    return tuple(sorted({a.strip() for a in raw if isinstance(a, str) and a.strip()}))


def alias_candidates(entity_registry: list[dict[str, Any]], exposed: set[str] | None) -> list[str]:
    """별칭을 따로 조회해야 하는 entity_id. 허용 도메인이고 노출된 엔티티만이다."""
    return sorted(
        entity_id
        for entry in entity_registry
        if (entity_id := str(entry.get("entity_id", ""))).partition(".")[0] in ALLOWED_DOMAINS
        and (exposed is None or entity_id in exposed)
    )


def parse_entity_aliases(entries: Any) -> dict[str, tuple[str, ...]]:
    """`config/entity_registry/get_entries` 결과에서 entity_id 별 별칭을 뽑는다."""
    if not isinstance(entries, dict):
        return {}
    return {
        entity_id: aliases
        for entity_id, entry in entries.items()
        if isinstance(entry, dict) and (aliases := clean_aliases(entry.get("aliases")))
    }


def to_entity(
    raw_state: dict[str, Any],
    area_id: str = "",
    registry_name: str = "",
    aliases: tuple[str, ...] = (),
) -> HomeEntity | None:
    """HA state 객체 하나를 HomeEntity 로. 허용 도메인이 아니면 None."""
    entity_id = str(raw_state.get("entity_id", ""))
    domain = entity_id.partition(".")[0]
    if domain not in ALLOWED_DOMAINS:
        return None
    raw_attributes = raw_state.get("attributes") or {}
    attributes = {
        key: str(raw_attributes[key])
        for key in KEPT_ATTRIBUTES
        if raw_attributes.get(key) is not None
    }
    name = attributes.get("friendly_name") or registry_name or entity_id
    return HomeEntity(
        entity_id=entity_id,
        area_id=area_id,
        name=name,
        domain=domain,
        state=str(raw_state.get("state", "")),
        attributes=attributes,
        changed_epoch_ms=parse_epoch_ms(raw_state.get("last_changed")),
        aliases=aliases,
    )


def build_catalog(
    states: list[dict[str, Any]],
    areas: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    entity_registry: list[dict[str, Any]],
    exposed: set[str] | None,
    entity_aliases: dict[str, tuple[str, ...]] | None = None,
) -> HomeCatalog:
    """레지스트리 3종과 state 목록으로 캐시를 만든다.

    Args:
        states: `get_states` 결과.
        areas: `config/area_registry/list` 결과.
        devices: `config/device_registry/list` 결과.
        entity_registry: `config/entity_registry/list` 결과.
        exposed: Assist 노출 entity_id 집합. None 이면 노출 설정을 알 수 없으므로
            허용 도메인 전체를 쓴다.
        entity_aliases: `parse_entity_aliases` 결과. 엔티티 레지스트리 목록 응답에는 별칭이
            없어 따로 조회한다. 목록 항목에 별칭이 있으면 그것을 보조로 쓴다.

    Returns:
        area 와 브리지 대상 엔티티. 엔티티 area 는 엔티티 레지스트리 값이 우선이고,
        없으면 소속 디바이스의 area 를 쓴다.
    """
    device_areas = {d["id"]: d.get("area_id") or "" for d in devices if "id" in d}
    registry = {e["entity_id"]: e for e in entity_registry if "entity_id" in e}

    catalog = HomeCatalog(
        areas={
            a["area_id"]: HomeArea(
                area_id=a["area_id"],
                name=a.get("name") or a["area_id"],
                aliases=clean_aliases(a.get("aliases")),
            )
            for a in areas
            if "area_id" in a
        }
    )
    for raw_state in states:
        entity_id = str(raw_state.get("entity_id", ""))
        if exposed is not None and entity_id not in exposed:
            continue
        reg = registry.get(entity_id, {})
        area_id = reg.get("area_id") or device_areas.get(reg.get("device_id") or "", "")
        aliases = (entity_aliases or {}).get(entity_id) or clean_aliases(reg.get("aliases"))
        entity = to_entity(
            raw_state, area_id, reg.get("name") or reg.get("original_name") or "", aliases
        )
        if entity is not None:
            catalog.entities[entity_id] = entity
    return catalog


def catalog_fingerprint(catalog: HomeCatalog) -> tuple[Any, ...]:
    """클라이언트에 보이는 구조(area 목록, 엔티티 집합·이름·소속 area·별칭)의 비교용 값.

    상태 값은 포함하지 않는다. 상태는 state_changed 로 따로 전달되기 때문이다.
    """
    areas = tuple(sorted((a.area_id, a.name, a.aliases) for a in catalog.areas.values()))
    entities = tuple(
        sorted(
            (e.entity_id, e.area_id, e.name, e.domain, e.aliases) for e in catalog.entities.values()
        )
    )
    return (areas, entities)


def state_to_proto(entity: HomeEntity) -> EntityState:
    """캐시 엔티티의 상태 부분을 protobuf 로."""
    return EntityState(
        state=entity.state,
        attributes=entity.attributes,
        changed_epoch_ms=entity.changed_epoch_ms,
    )


def entity_to_proto(entity: HomeEntity) -> Entity:
    """캐시 엔티티를 protobuf 로."""
    return Entity(
        entity_id=entity.entity_id,
        area_id=entity.area_id,
        name=entity.name,
        domain=entity.domain,
        state=state_to_proto(entity),
        aliases=entity.aliases,
    )


def snapshot_to_proto(catalog: HomeCatalog) -> HomeSnapshot:
    """캐시 전체를 HomeSnapshot 으로."""
    return HomeSnapshot(
        areas=[
            Area(area_id=a.area_id, name=a.name, aliases=a.aliases) for a in catalog.areas.values()
        ],
        entities=[entity_to_proto(e) for e in catalog.entities.values()],
    )


def changed_to_proto(entity: HomeEntity) -> EntityStateChanged:
    """상태가 바뀐 엔티티를 EntityStateChanged 로."""
    return EntityStateChanged(entity_id=entity.entity_id, state=state_to_proto(entity))
