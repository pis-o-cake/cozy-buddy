"""엔티티 제어 허용 정책. 음성(LLM 포함)·터치 경로가 모두 이 함수 하나를 거친다."""

from collections.abc import Collection

from app.domain.home.schemas import HomeEntity

# HA 노출 목록을 읽지 못했을 때 제어를 허용할 도메인. 위험이 낮은 조명류만 둔다.
FALLBACK_CONTROL_DOMAINS: frozenset[str] = frozenset({"light", "switch", "fan"})
# 음성으로 열리면 안 되는 cover 의 device_class.
VOICE_BLOCKED_COVER_CLASSES: frozenset[str] = frozenset({"garage", "gate", "door"})
# cover 를 여는 방향의 동작. device_class 를 모르는 cover 는 문일 수 있어 음성으로 이 동작을 막는다.
COVER_OPENING_ACTIONS: frozenset[str] = frozenset({"turn_on", "toggle"})
# 어떤 경로로도 제어하지 않는 도메인. commands.py 가 서비스 매핑을 두지 않는 것과 이중으로 막는다.
NEVER_CONTROL_DOMAINS: frozenset[str] = frozenset({"lock", "alarm_control_panel"})
# 제어 대상이 될 수 있는 도메인(commands.py 의 서비스 매핑과 같아야 한다).
CONTROLLABLE_DOMAINS: frozenset[str] = frozenset(
    {"light", "switch", "fan", "cover", "climate", "media_player", "scene", "script"}
)


def can_control(
    entity: HomeEntity,
    *,
    exposure_known: bool,
    allowed_scripts: Collection[str],
    voice: bool,
    allowed_scenes: Collection[str] = frozenset(),
    voice_blocked_entities: Collection[str] = frozenset(),
    action: str | None = None,
) -> bool:
    """엔티티를 제어해도 되는지.

    Args:
        entity: 제어 대상.
        exposure_known: HA Assist 노출 목록을 읽었는지. False 면 폴백 도메인만 허용한다.
        allowed_scripts: 실행을 허용한 script 엔티티 ID. 터치·음성 모두 여기 없으면 거절한다.
        voice: 음성/LLM 경로인지. 터치(허브·앱 EntityCommand)는 차고·대문 열기를 허용한다.
        allowed_scenes: 음성으로 활성화를 허용한 scene 엔티티 ID. 터치 경로에는 적용하지 않는다.
        voice_blocked_entities: 음성으로 제어하지 않을 엔티티 ID(도메인 무관). 문을 여는 switch 용.
        action: 실행하려는 동작. None 이면 후보 거르기라서 동작 기준 제한은 건너뛴다.
    """
    domain = entity.domain
    if domain in NEVER_CONTROL_DOMAINS or domain not in CONTROLLABLE_DOMAINS:
        return False
    if not exposure_known and domain not in FALLBACK_CONTROL_DOMAINS:
        return False
    if domain == "script":
        return entity.entity_id in allowed_scripts
    if not voice:
        return True
    if entity.entity_id in voice_blocked_entities:
        return False
    if domain == "scene":
        return entity.entity_id in allowed_scenes
    if domain == "cover":
        return _voice_cover_allowed(entity, action)
    return True


def _voice_cover_allowed(entity: HomeEntity, action: str | None) -> bool:
    device_class = entity.attributes.get("device_class", "")
    if device_class in VOICE_BLOCKED_COVER_CLASSES:
        return False
    # IMPORTANT: device_class 가 없는 cover 는 문·차고일 수 있어 여는 동작만 막고 닫기는 허용한다.
    if not device_class:
        return action not in COVER_OPENING_ACTIONS
    return True
