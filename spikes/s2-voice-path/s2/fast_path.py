"""Naive keyword matcher + fixed templates: a latency proxy for the server's deterministic fast path.

Not the product matcher. Handles only simple on/off/brightness/timer/alarm commands; anything
ambiguous (questions, multiple devices, chit-chat) returns None and falls back to the LLM path.
"""
from __future__ import annotations

import re
from typing import Any

from s2.home import HOME
from s2.normalize import normalize_numbers

AREA_WORDS = {"거실": "living", "침실": "bedroom", "주방": "kitchen"}
AREA_NAMES = {"living": "거실", "bedroom": "침실", "kitchen": "주방"}
DEVICE_WORDS = [("가습기", "humidifier"), ("포트", "kettle_plug"), ("스탠드", "stand"),
                ("조명", "light"), ("불", "light")]
DEVICE_NAMES = {"light": "조명", "stand": "스탠드", "humidifier": "가습기", "kettle_plug": "전기포트"}
_REJECT = ("?", "있", "져", "이랑", "하고", "고 ", "도 ", "랑")
_PM = ("오후", "저녁", "밤")

TEMPLATES = {
    "on": "{area} {device} 켰어요.",
    "off": "{area} {device} 껐어요.",
    "set_brightness": "{area} {device} 밝기를 {value}퍼센트로 맞췄어요.",
    "set_timer": "{minutes}분 타이머를 시작할게요.",
    "set_alarm": "{period} {hour}시 {minute}분 알람을 맞췄어요.",
}


def _find_area(text: str) -> str:
    for word, area in AREA_WORDS.items():
        if word in text:
            return area
    return "living"


def _match_timer(text: str) -> dict[str, Any] | None:
    m = re.search(r"(\d+)\s*분", text)
    if "타이머" not in text or not m:
        return None
    return {"name": "set_timer", "args": {"minutes": int(m.group(1))}}


def _match_alarm(text: str) -> dict[str, Any] | None:
    m = re.search(r"(\d+)\s*시(?:\s*(\d+)\s*분)?", text)
    if not ("알람" in text or "깨워" in text) or not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if any(w in text for w in _PM) and hour < 12:
        hour += 12
    return {"name": "set_alarm", "args": {"time_hh_mm": f"{hour:02d}:{minute:02d}"}}


def _match_device(text: str) -> dict[str, Any] | None:
    devices = {dev for word, dev in DEVICE_WORDS if word in text}
    if len(devices) != 1:
        return None
    device = devices.pop()
    area = _find_area(text)
    if device not in HOME[area]:
        return None
    args: dict[str, Any] = {"area": area, "device": device}
    if "밝기" in text:
        m = re.search(r"(\d+)", text)
        if not m:
            return None
        return {"name": "control_device", "args": {**args, "action": "set_brightness", "value": int(m.group(1))}}
    on, off = "켜" in text or "틀" in text, "꺼" in text or "끄" in text
    if on == off:
        return None
    return {"name": "control_device", "args": {**args, "action": "on" if on else "off"}}


def match(transcript: str) -> list[dict[str, Any]] | None:
    """Return the tool calls for a simple command, or None when unmatched."""
    text = normalize_numbers(transcript)
    if any(tok in text for tok in _REJECT):
        return None
    for fn in (_match_timer, _match_alarm, _match_device):
        call = fn(text)
        if call:
            return [call]
    return None


def render(call: dict[str, Any]) -> str:
    """Fixed Korean template response for a matched call."""
    a = call["args"]
    if call["name"] == "set_timer":
        return TEMPLATES["set_timer"].format(minutes=a["minutes"])
    if call["name"] == "set_alarm":
        hour, minute = (int(x) for x in a["time_hh_mm"].split(":"))
        period = "오후" if hour >= 12 else "오전"
        return TEMPLATES["set_alarm"].format(period=period, hour=hour % 12 or 12, minute=minute)
    return TEMPLATES[a["action"]].format(area=AREA_NAMES[a["area"]], device=DEVICE_NAMES[a["device"]],
                                         value=a.get("value"))
