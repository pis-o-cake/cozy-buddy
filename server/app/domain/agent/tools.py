"""LLM 폴백에 노출하는 툴 정의. 현재 집의 엔티티로 매번 새로 만든다."""

from typing import Any

from app.domain.agent.home_view import HomeView

TOOL_CONTROL = "control_device"
TOOL_STATE = "get_device_state"
TOOL_SET_ALARM = "set_alarm"
TOOL_SET_TIMER = "set_timer"
TOOL_CANCEL = "cancel_alarm"

CONTROL_ACTIONS = ("turn_on", "turn_off", "toggle", "set_brightness", "activate")
CONTROLLABLE_DOMAINS = frozenset(
    {"light", "switch", "fan", "cover", "climate", "media_player", "scene", "script"}
)
MAX_TIMER_SECONDS = 24 * 3600


def _catalog(view: HomeView, domains: frozenset[str] | None) -> tuple[list[str], str]:
    ids: list[str] = []
    lines: list[str] = []
    for entity in view.model.entities:
        if domains is not None and entity.domain not in domains:
            continue
        ids.append(entity.entity_id)
        area = view.area_name(entity.area_id)
        label = f"{area} {entity.name}".strip()
        lines.append(f"{entity.entity_id}={label}")
    return ids, "; ".join(lines)


def _function(name: str, description: str, properties: dict[str, Any], required: list[str]) -> Any:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def build_tools(view: HomeView) -> list[Any]:
    """현재 집에 맞춘 툴 목록을 만든다. 기기가 없으면 기기 툴은 뺀다.

    Args:
        view: 현재 집의 구조와 상태.

    Returns:
        OpenAI chat `tools` 형식의 목록.
    """
    tools: list[Any] = []
    control_ids, control_catalog = _catalog(view, CONTROLLABLE_DOMAINS)
    if control_ids:
        tools.append(
            _function(
                TOOL_CONTROL,
                f"Control a device or run a scene. Entities: {control_catalog}",
                {
                    "entity_id": {"type": "string", "enum": control_ids},
                    "action": {"type": "string", "enum": list(CONTROL_ACTIONS)},
                    "brightness_pct": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Absolute brightness for set_brightness.",
                    },
                    "brightness_delta_pct": {
                        "type": "integer",
                        "minimum": -100,
                        "maximum": 100,
                        "description": "Relative brightness change for set_brightness.",
                    },
                },
                ["entity_id", "action"],
            )
        )
    state_ids, state_catalog = _catalog(view, None)
    if state_ids:
        tools.append(
            _function(
                TOOL_STATE,
                f"Read the current state of an entity. Entities: {state_catalog}",
                {"entity_id": {"type": "string", "enum": state_ids}},
                ["entity_id"],
            )
        )
    tools.append(
        _function(
            TOOL_SET_ALARM,
            "Set an alarm on this hub. Without days and date it rings once at the next "
            "occurrence of the time.",
            {
                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "minute": {"type": "integer", "minimum": 0, "maximum": 59},
                "days": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 7},
                    "description": "Repeat days, ISO weekday 1=Monday..7=Sunday.",
                },
                "date": {"type": "string", "description": "One-shot date, YYYY-MM-DD."},
                "label": {"type": "string"},
            },
            ["hour", "minute"],
        )
    )
    tools.append(
        _function(
            TOOL_SET_TIMER,
            "Start a countdown timer on this hub.",
            {
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TIMER_SECONDS,
                },
                "label": {"type": "string"},
            },
            ["duration_seconds"],
        )
    )
    tools.append(
        _function(
            TOOL_CANCEL,
            "Cancel an alarm or timer on this hub. Give label and/or time to select it.",
            {
                "label": {"type": "string"},
                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "minute": {"type": "integer", "minimum": 0, "maximum": 59},
                "is_timer": {"type": "boolean"},
            },
            [],
        )
    )
    return tools
