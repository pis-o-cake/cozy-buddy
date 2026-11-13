"""Fake smart home: tool schemas, deterministic executor, Korean system prompt."""
from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = (
    "너는 거실에 놓인 스마트홈 음성 비서 '코지'다. 사용자는 말로 명령한다. "
    "한국어로 한두 문장, 짧게 답하고 마크다운과 이모지는 쓰지 않는다. 음성으로 읽히는 문장이다. "
    "방을 말하지 않으면 지금 말하는 허브가 있는 거실이다. "
    "기기를 제어하거나 상태를 물을 때, 알람과 타이머는 반드시 도구를 호출한다. "
    "여러 기기를 한 번에 말하면 도구를 각각 호출한다. 잡담과 일반 질문은 도구 없이 답한다. "
    "집 구성: 거실(living)=조명(light), 스탠드(stand). 침실(bedroom)=조명(light), 가습기(humidifier). "
    "주방(kitchen)=조명(light), 전기포트 플러그(kettle_plug)."
)

HOME: dict[str, set[str]] = {
    "living": {"light", "stand"},
    "bedroom": {"light", "humidifier"},
    "kitchen": {"light", "kettle_plug"},
}

_AREAS = sorted(HOME)
_DEVICES = ["light", "stand", "humidifier", "kettle_plug"]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "control_device",
        "description": "기기를 켜거나 끄거나 밝기를 설정한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": _AREAS},
                "device": {"type": "string", "enum": _DEVICES},
                "action": {"type": "string", "enum": ["on", "off", "set_brightness"]},
                "value": {"type": "integer", "description": "set_brightness일 때 밝기 0-100"},
            },
            "required": ["area", "device", "action"],
        },
    },
    {
        "name": "get_device_state",
        "description": "기기의 현재 상태를 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": _AREAS},
                "device": {"type": "string", "enum": _DEVICES},
            },
            "required": ["area", "device"],
        },
    },
    {
        "name": "set_alarm",
        "description": "알람을 설정한다. 24시간제 HH:MM.",
        "parameters": {
            "type": "object",
            "properties": {
                "time_hh_mm": {"type": "string", "description": "예: 07:30"},
                "label": {"type": "string"},
            },
            "required": ["time_hh_mm"],
        },
    },
    {
        "name": "set_timer",
        "description": "타이머를 분 단위로 설정한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer"},
                "label": {"type": "string"},
            },
            "required": ["minutes"],
        },
    },
]

CHAT_TOOLS = [{"type": "function", "function": t} for t in TOOLS]
REALTIME_TOOLS = [{"type": "function", **t} for t in TOOLS]


def execute_tool(name: str, arguments: str | dict[str, Any]) -> str:
    """Deterministic fake executor; returns a JSON string."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "error": "invalid_json"})
    if name in ("control_device", "get_device_state"):
        area, device = args.get("area"), args.get("device")
        if device not in HOME.get(area, set()):
            return json.dumps({"ok": False, "error": "device_not_found"})
        if name == "get_device_state":
            return json.dumps({"ok": True, "area": area, "device": device, "state": "on", "brightness": 60})
        return json.dumps({"ok": True, "area": area, "device": device,
                           "action": args.get("action"), "value": args.get("value")})
    if name == "set_alarm":
        return json.dumps({"ok": True, "time_hh_mm": args.get("time_hh_mm")})
    if name == "set_timer":
        return json.dumps({"ok": True, "minutes": args.get("minutes")})
    return json.dumps({"ok": False, "error": "unknown_tool"})


def is_correct(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> bool:
    """Order-insensitive exact match on tool name and required args; extra calls fail."""
    if len(expected) != len(actual):
        return False
    remaining = list(actual)
    for exp in expected:
        for i, act in enumerate(remaining):
            if act["name"] == exp["name"] and _args_match(exp["args"], act["args"]):
                del remaining[i]
                break
        else:
            return False
    return True


def _args_match(exp: dict[str, Any], act: dict[str, Any]) -> bool:
    for key, val in exp.items():
        got = act.get(key)
        if key in ("value", "minutes"):
            try:
                if int(got) != int(val):
                    return False
            except (TypeError, ValueError):
                return False
        elif got != val:
            return False
    return True
