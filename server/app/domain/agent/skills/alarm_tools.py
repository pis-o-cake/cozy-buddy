"""알람·타이머 LLM 툴 정의."""

from typing import Any

from app.domain.agent.skills.tooling import function_tool

TOOL_SET_ALARM = "set_alarm"
TOOL_SET_TIMER = "set_timer"
TOOL_CANCEL = "cancel_alarm"
MAX_TIMER_SECONDS = 24 * 3600


def alarm_tool_specs() -> list[dict[str, Any]]:
    """알람 설정, 타이머 설정, 취소 툴 정의."""
    return [
        function_tool(
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
        ),
        function_tool(
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
        ),
        function_tool(
            TOOL_CANCEL,
            "Cancel an alarm or timer on this hub. Give label and/or time to select it.",
            {
                "label": {"type": "string"},
                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "minute": {"type": "integer", "minimum": 0, "maximum": 59},
                "is_timer": {"type": "boolean"},
            },
            [],
        ),
    ]
