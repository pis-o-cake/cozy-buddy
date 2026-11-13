"""매처 결과를 템플릿 응답(언어팩 키 + 인자)으로 바꾼다.

문구는 locales/ko.json 에만 둔다. 조사는 "을(를)", "이(가)" 같은 표지로 적고 render 에서
앞 단어의 받침을 보고 확정한다.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.agent.model import (
    AlarmIntent,
    CancelAlarmIntent,
    Clarify,
    ClarifyReason,
    DeviceAction,
    DeviceCommand,
    EntityRef,
    HomeModel,
    MatchResult,
    SceneCommand,
    TimerIntent,
)
from app.domain.agent.normalize import entity_base_name

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JONG_NONE = 0
_JONG_RIEUL = 8
# 숫자와 %의 한글 읽기에서 받침 인덱스. 표에 없는 숫자는 받침이 없다.
_DIGIT_JONG = {"0": 21, "1": 8, "3": 16, "6": 1, "7": 8, "8": 8}
_JOSA_RE = re.compile(
    r"([가-힣A-Za-z0-9%]+)(을\(를\)|이\(가\)|은\(는\)|으로\(로\)|와\(과\)|\(이\)에요)"
)
_JOSA_FORMS = {
    "을(를)": ("을", "를"),
    "이(가)": ("이", "가"),
    "은(는)": ("은", "는"),
    "와(과)": ("과", "와"),
    "(이)에요": ("이에요", "예요"),
}
_SPACE_RE = re.compile(r"\s+")
_MINUTES_HALF = 30
_EVERY_DAY = (1, 2, 3, 4, 5, 6, 7)
_WEEKDAYS = (1, 2, 3, 4, 5)
_WEEKEND = (6, 7)
_NOON_HOUR = 12
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_MINUTE = 60


@dataclass(frozen=True, slots=True)
class TemplateResponse:
    """언어팩 키와 채울 인자. 문구는 render 가 만든다."""

    key: str
    args: Mapping[str, str] = field(default_factory=dict)


def _final_jong(char: str) -> int:
    code = ord(char)
    if _HANGUL_BASE <= code <= _HANGUL_LAST:
        return (code - _HANGUL_BASE) % 28
    return _DIGIT_JONG.get(char, _JONG_NONE)


def josa(word: str, marker: str) -> str:
    """단어 끝 받침에 맞는 조사를 고른다.

    Args:
        word: 조사가 붙을 단어.
        marker: "을(를)", "이(가)", "은(는)", "으로(로)", "와(과)", "(이)에요" 중 하나.

    Returns:
        조사 형태. 영문자는 받침이 없다고 본다.
    """
    jong = _final_jong(word[-1]) if word else _JONG_NONE
    if marker == "으로(로)":
        return "로" if jong in (_JONG_NONE, _JONG_RIEUL) else "으로"
    with_batchim, without = _JOSA_FORMS[marker]
    return with_batchim if jong != _JONG_NONE else without


def resolve_josa(text: str) -> str:
    """문장 속 조사 표지를 앞 단어의 받침에 맞게 확정한다."""
    return _JOSA_RE.sub(lambda m: m.group(1) + josa(m.group(1), m.group(2)), text)


def render(response: TemplateResponse, translate: Callable[[str], str]) -> str:
    """응답을 최종 문장으로 만든다.

    Args:
        response: build_response 등이 만든 템플릿 응답.
        translate: 언어팩 조회 함수(예: core.i18n.Translator).
    """
    text = translate(response.key).format_map(dict(response.args))
    return _SPACE_RE.sub(" ", resolve_josa(text)).strip()


def _area_name(entity: EntityRef, home: HomeModel) -> str:
    return next((a.name for a in home.areas if a.area_id == entity.area_id), "")


def _device_name(entity: EntityRef, home: HomeModel) -> str:
    return entity_base_name(entity.name, _area_name(entity, home) or None)


def _label(entity: EntityRef, home: HomeModel) -> str:
    return _SPACE_RE.sub(" ", f"{_area_name(entity, home)} {_device_name(entity, home)}").strip()


def _entities(ids: tuple[str, ...], home: HomeModel) -> list[EntityRef]:
    by_id = {e.entity_id: e for e in home.entities}
    return [by_id[i] for i in ids if i in by_id]


def _clarify(result: Clarify, home: HomeModel) -> TemplateResponse:
    entities = _entities(result.candidate_entity_ids, home)
    if result.reason is ClarifyReason.AMBIGUOUS_TARGET:
        options = ", ".join(_label(e, home) for e in entities)
        return TemplateResponse("agent.clarify.ambiguous", {"options": options})
    if not entities:
        return TemplateResponse("agent.clarify.broad_alarm")
    return TemplateResponse("agent.clarify.broad_action", {"count": str(len(entities))})


Translate = Callable[[str], str]


def _device(
    result: DeviceCommand, home: HomeModel, translate: Translate
) -> TemplateResponse | None:
    entities = _entities(result.entity_ids, home)
    if not entities:
        return None
    is_cover = all(e.domain == "cover" for e in entities)
    pct = result.params.brightness_pct
    delta = result.params.brightness_delta_pct
    if result.action is DeviceAction.SET_BRIGHTNESS and pct is not None:
        verb = "brightness_set"
    elif result.action is DeviceAction.SET_BRIGHTNESS:
        verb = "brighter" if (delta or 0) > 0 else "darker"
    elif result.action is DeviceAction.TOGGLE:
        verb = "toggle"
    elif result.action is DeviceAction.TURN_ON:
        verb = "open" if is_cover else "on"
    else:
        verb = "close" if is_cover else "off"

    areas = dict.fromkeys(_area_name(e, home) for e in entities if _area_name(e, home))
    args = {"area": ", ".join(areas), "pct": "" if pct is None else str(pct)}
    if len(entities) == 1:
        args["device"] = _device_name(entities[0], home)
        return TemplateResponse(f"agent.device.{verb}", args)
    domains = {e.domain for e in entities}
    noun_key = f"agent.domain.{next(iter(domains))}" if len(domains) == 1 else "agent.domain.mixed"
    args.update(device=translate(noun_key), count=str(len(entities)))
    return TemplateResponse(f"agent.device.{verb}_many", args)


def _scene(result: SceneCommand, home: HomeModel) -> TemplateResponse | None:
    entities = _entities((result.entity_id,), home)
    if not entities:
        return None
    return TemplateResponse("agent.scene.run", {"scene": entities[0].name})


def _time_text(hour: int, minute: int, translate: Translate, *, meridiem: bool) -> str:
    """ "오전 7시 30분" 같은 시각 문구. meridiem 이 False 면 말한 그대로("7시")."""
    prefix = "agent.time.meridiem" if meridiem else "agent.time.plain"
    suffix = "h" if minute == 0 else "half" if minute == _MINUTES_HALF else "hm"
    shown = hour % 12 or 12 if meridiem else hour
    part = translate("agent.time.am" if hour < _NOON_HOUR else "agent.time.pm")
    template = translate(f"{prefix}_{suffix}")
    return template.format(meridiem=part, hour=shown, minute=minute)


def _days_text(days: tuple[int, ...], translate: Translate) -> str:
    if days == _EVERY_DAY:
        return translate("agent.days.everyday")
    if days == _WEEKDAYS:
        return translate("agent.days.weekdays")
    if days == _WEEKEND:
        return translate("agent.days.weekend")
    return ", ".join(translate(f"agent.day.{d}") for d in days)


def _date_text(epoch_ms: int, now: datetime, translate: Translate) -> str:
    target = datetime.fromtimestamp(epoch_ms / 1000, tz=now.tzinfo)
    keys = {0: "agent.date.today", 1: "agent.date.tomorrow", 2: "agent.date.day_after"}
    offset = (target.date() - now.date()).days
    if offset in keys:
        return translate(keys[offset])
    return translate("agent.date.month_day").format(month=target.month, day=target.day)


def _alarm(result: AlarmIntent, now: datetime, translate: Translate) -> TemplateResponse:
    time_text = _time_text(result.hour, result.minute, translate, meridiem=True)
    if result.days is not None:
        when = f"{_days_text(result.days, translate)} {time_text}"
        suffix = "set_repeat"
    else:
        date_text = _date_text(result.one_shot_epoch_ms or 0, now, translate)
        when = f"{date_text} {time_text}"
        suffix = "set_once"
    args = {"when": when, "label": result.label or ""}
    return TemplateResponse(f"agent.alarm.{suffix}{'_label' if result.label else ''}", args)


def _timer(result: TimerIntent, translate: Translate) -> TemplateResponse:
    hours, rest = divmod(result.duration_seconds, _SECONDS_PER_HOUR)
    minutes, seconds = divmod(rest, _SECONDS_PER_MINUTE)
    parts = [
        translate(key).format(n=value)
        for key, value in (
            ("agent.duration.hours", hours),
            ("agent.duration.minutes", minutes),
            ("agent.duration.seconds", seconds),
        )
        if value
    ]
    args = {"duration": " ".join(parts), "label": result.label or ""}
    return TemplateResponse(f"agent.timer.set{'_label' if result.label else ''}", args)


def _cancel(result: CancelAlarmIntent, translate: Translate) -> TemplateResponse:
    kind = "timer" if result.is_timer else "alarm"
    if result.label:
        return TemplateResponse(f"agent.{kind}.cancel_label", {"label": result.label})
    if result.hour is not None and result.minute is not None:
        text = _time_text(result.hour, result.minute, translate, meridiem=not result.hour_ambiguous)
        return TemplateResponse("agent.alarm.cancel_time", {"time": text})
    return TemplateResponse(f"agent.{kind}.cancel")


def build_response(
    result: MatchResult, home: HomeModel, now: datetime, translate: Translate
) -> TemplateResponse | None:
    """매처 결과를 템플릿 응답으로 바꾼다. NoMatch/StateQuery 는 None.

    StateQuery 는 현재 상태가 있어야 답할 수 있으므로 build_state_response 를 쓴다.

    Args:
        result: match() 결과.
        home: 이름을 풀 집 구조 스냅샷.
        now: 알람 날짜 표현("내일")의 기준 시각.
        translate: 시각·요일 같은 조각 문구를 푸는 언어팩 조회 함수.
    """
    if isinstance(result, DeviceCommand):
        return _device(result, home, translate)
    if isinstance(result, SceneCommand):
        return _scene(result, home)
    if isinstance(result, AlarmIntent):
        return _alarm(result, now, translate)
    if isinstance(result, TimerIntent):
        return _timer(result, translate)
    if isinstance(result, CancelAlarmIntent):
        return _cancel(result, translate)
    if isinstance(result, Clarify):
        return _clarify(result, home)
    return None


_STATE_KEYS = {
    "on": "agent.query.on",
    "off": "agent.query.off",
    "open": "agent.query.open",
    "closed": "agent.query.closed",
}


def build_state_response(home: HomeModel, entity_id: str, state: str) -> TemplateResponse | None:
    """상태 질의에 대한 답. state 는 HA 상태 문자열(on/off/open/closed) 또는 센서 값."""
    entities = _entities((entity_id,), home)
    if not entities:
        return None
    entity = entities[0]
    args = {"area": _area_name(entity, home), "device": _device_name(entity, home)}
    key = _STATE_KEYS.get(state)
    if key is not None and entity.domain != "sensor":
        return TemplateResponse(key, args)
    return TemplateResponse("agent.query.value", {**args, "value": state})
