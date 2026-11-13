"""EntityCommand.action → HA 서비스 호출 매핑."""

from collections.abc import Mapping

from app.domain.home.schemas import ServiceCall

TURN_ACTIONS = frozenset({"turn_on", "turn_off", "toggle"})
TURNABLE_DOMAINS = frozenset({"light", "switch", "fan", "climate", "media_player"})
# cover 는 turn_* 서비스가 없어 열기/닫기로 옮긴다.
COVER_SERVICES = {"turn_on": "open_cover", "turn_off": "close_cover", "toggle": "toggle"}


class UnsupportedCommandError(ValueError):
    """도메인이 지원하지 않는 동작이거나 파라미터가 잘못됐다."""


def build_service_call(domain: str, action: str, params: Mapping[str, str]) -> ServiceCall:
    """엔티티 도메인과 동작을 HA 서비스 호출로 바꾼다.

    Raises:
        UnsupportedCommandError: 알 수 없는 동작, 도메인과 맞지 않는 동작, 잘못된 파라미터.
    """
    if action in TURN_ACTIONS:
        return _turn(domain, action)
    if action == "set_brightness":
        return _brightness(domain, params)
    if action == "activate" and domain == "scene":
        return ServiceCall(domain="scene", service="turn_on")
    if action == "run" and domain == "script":
        return ServiceCall(domain="script", service="turn_on")
    raise UnsupportedCommandError(f"Unsupported action {action!r} for domain {domain!r}")


def _turn(domain: str, action: str) -> ServiceCall:
    if domain == "cover":
        return ServiceCall(domain=domain, service=COVER_SERVICES[action])
    if domain in TURNABLE_DOMAINS:
        return ServiceCall(domain=domain, service=action)
    raise UnsupportedCommandError(f"Action {action!r} not supported for domain {domain!r}")


def _brightness(domain: str, params: Mapping[str, str]) -> ServiceCall:
    if domain != "light":
        raise UnsupportedCommandError("set_brightness is only supported for lights")
    try:
        pct = int(params.get("brightness_pct", ""))
    except ValueError as exc:
        raise UnsupportedCommandError("brightness_pct must be an integer") from exc
    if not 0 <= pct <= 100:
        raise UnsupportedCommandError("brightness_pct must be within 0..100")
    if pct == 0:
        return ServiceCall(domain="light", service="turn_off")
    return ServiceCall(domain="light", service="turn_on", data={"brightness_pct": pct})
