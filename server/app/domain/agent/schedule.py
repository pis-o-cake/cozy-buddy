"""알람·타이머·취소 발화 해석. 순수 함수만 둔다."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.agent.model import (
    AlarmIntent,
    CancelAlarmIntent,
    Clarify,
    ClarifyReason,
    MatchResult,
    NoMatch,
    TimerIntent,
)
from app.domain.agent.normalize import NormalizedText

_CLOCK_RE = re.compile(r"(\d{1,2})시(?!간)(?:\s*(?:(\d{1,2})분|(반)))?(?:\s*정각)?(?:에|쯤|부터)?")
_DURATION_RE = re.compile(r"(?:(\d+)시간(\s*반)?)?\s*(?:(\d+)분)?\s*(?:(\d+)초)?")
_GLUED_RE = re.compile(r"^(내일|오늘|모레)?(오전|오후|아침|저녁|밤|새벽|낮|점심)?$")
_LABEL_RE = re.compile(r"^[가-힣a-z]{1,8}$")

_MAX_TIMER_SECONDS = 24 * 3600
_PARTICLE_SUFFIXES = ("에는", "에", "은", "는", "을", "를", "이", "가", "도", "쯤", "부터", "까지")
_DAY_REL = {"오늘": 0, "내일": 1, "모레": 2}
_MERIDIEM = {
    "오전": "am", "아침": "am", "새벽": "am", "오후": "pm", "저녁": "pm", "밤": "night",
    "낮": "noon", "점심": "noon",
}  # fmt: skip
_RECURRING = {"매일": (1, 2, 3, 4, 5, 6, 7), "평일": (1, 2, 3, 4, 5), "주중": (1, 2, 3, 4, 5)}
_WEEKEND = (6, 7)
_WEEKDAYS = {
    "월요일": 1, "화요일": 2, "수요일": 3, "목요일": 4, "금요일": 5, "토요일": 6, "일요일": 7,
}  # fmt: skip
_ALARM_WORDS = frozenset({"알람", "모닝콜"})
_TIMER_WORDS = frozenset({"타이머"})
_WAKE_VERBS = frozenset({"깨워", "울려", "알려"})
_SET_VERBS = frozenset(
    {"맞춰", "설정", "등록", "추가", "만들어", "세팅", "저장", "켜", "시작", "돌려"}
)
_REL_WORDS = frozenset({"뒤", "후", "이따", "이따가", "있다", "있다가"})
_CANCEL_VERBS = frozenset(
    {
        "취소",
        "삭제",
        "지워",
        "지우",
        "없애",
        "해제",
        "꺼",
        "끄",
        "멈춰",
        "중지",
        "정지",
        "그만",
        "종료",
    }
)
_ALL_WORDS = frozenset({"모든", "전부", "다", "모두", "전체"})
_NOISE_WORDS = frozenset({"정각", "짜리", "동안"})
_AMBIGUOUS_EARLY_HOURS = (1, 2, 3, 4)


@dataclass(slots=True)
class _Tokens:
    """토큰을 범주별로 모은 결과."""

    meridiem: list[str] = field(default_factory=list)
    rel_day: int | None = None
    weekdays: set[int] = field(default_factory=set)
    recurring: tuple[int, ...] | None = None
    weekly: bool = False
    alarm_word: bool = False
    timer_word: bool = False
    wake_verb: bool = False
    rel_word: bool = False
    cancel_verb: bool = False
    all_word: bool = False
    leftovers: list[str] = field(default_factory=list)
    invalid: bool = False


def _strip_suffix(token: str, known: set[str] | frozenset[str]) -> str | None:
    if token in known:
        return token
    for suffix in _PARTICLE_SUFFIXES:
        if token.endswith(suffix) and token[: -len(suffix)] in known:
            return token[: -len(suffix)]
    return None


def _classify(rest: str) -> _Tokens:
    tokens = _Tokens()
    for raw in rest.split():
        token = raw
        if token.endswith("마다") and len(token) > 2:
            tokens.weekly = True
            token = token[:-2]
        if _classify_one(token, tokens):
            continue
        tokens.leftovers.append(raw)
    return tokens


def _classify_one(token: str, tokens: _Tokens) -> bool:
    glued = _GLUED_RE.match(token)
    if glued is not None and token:
        day, meridiem = glued.groups()
        if day is not None:
            if tokens.rel_day is not None:
                tokens.invalid = True
            tokens.rel_day = _DAY_REL[day]
        if meridiem is not None:
            tokens.meridiem.append(meridiem)
        return True
    simple_sets: tuple[tuple[frozenset[str], str], ...] = (
        (_ALARM_WORDS, "alarm_word"),
        (_TIMER_WORDS, "timer_word"),
        (_WAKE_VERBS, "wake_verb"),
        (_REL_WORDS, "rel_word"),
        (_CANCEL_VERBS, "cancel_verb"),
        (_ALL_WORDS, "all_word"),
    )
    for words, attr in simple_sets:
        if _strip_suffix(token, words) is not None:
            setattr(tokens, attr, True)
            return True
    if _strip_suffix(token, _SET_VERBS | _NOISE_WORDS | {"해"}) is not None:
        return True
    if _strip_suffix(token, {"주말"}) is not None:
        tokens.recurring = _WEEKEND
        return True
    recurring = _strip_suffix(token, set(_RECURRING))
    if recurring is not None:
        tokens.recurring = _RECURRING[recurring]
        return True
    if token == "매주":
        tokens.weekly = True
        return True
    weekday = _strip_suffix(token, set(_WEEKDAYS))
    if weekday is not None:
        tokens.weekdays.add(_WEEKDAYS[weekday])
        return True
    return False


def _label(tokens: _Tokens) -> tuple[bool, str | None]:
    """남은 토큰이 라벨로 쓸 수 있는지 판정한다. (유효 여부, 라벨)."""
    if not tokens.leftovers:
        return True, None
    if len(tokens.leftovers) > 1 or not _LABEL_RE.match(tokens.leftovers[0]):
        return False, None
    return True, tokens.leftovers[0]


def _hour_candidates(hour: int, meridiem: str | None) -> tuple[int, ...]:
    """말한 시와 시간대 단어로 가능한 24시간제 시각 후보를 구한다. 불가능하면 빈 튜플."""
    if hour > 24 or (hour == 24 and meridiem is not None):
        return ()
    if hour == 24:
        return (0,)
    if meridiem is None:
        if hour == 0 or hour >= 13:
            return (hour,)
        return (hour % 12, hour % 12 + 12)
    if hour > 12:
        return ()
    if meridiem == "am":
        return (hour % 12,)
    if meridiem == "noon":
        return (12,) if hour == 12 else (hour + 12 if hour <= 5 else hour,)
    if hour == 12:
        return (0,) if meridiem == "night" else (12,)
    if meridiem == "night" and hour <= 4:
        return (hour,)
    return (hour + 12,)


def _guess_hour(candidates: tuple[int, ...]) -> int:
    """오전/오후가 불분명할 때의 관례: 5~11시 오전, 12시 정오, 1~4시 오후."""
    if len(candidates) == 1:
        return candidates[0]
    base = candidates[0]
    if base == 0:
        return 12
    return base if base >= 5 else base + 12


def _repeat_hour(candidates: tuple[int, ...]) -> int | None:
    """반복 알람은 오전/오후가 불분명한 1~4시를 추측하지 않는다."""
    if len(candidates) == 1 or candidates[0] == 0:
        return _guess_hour(candidates)
    if candidates[0] in _AMBIGUOUS_EARLY_HOURS:
        return None
    return _guess_hour(candidates)


def _at(now: datetime, day_offset: int, hour: int, minute: int) -> datetime:
    return (now + timedelta(days=day_offset)).replace(
        hour=hour % 24, minute=minute, second=0, microsecond=0
    )


def _next_once(
    now: datetime,
    candidates: tuple[int, ...],
    minute: int,
    rel_day: int | None,
    weekday: int | None,
) -> datetime | None:
    if weekday is not None:
        offset = (weekday - now.isoweekday()) % 7
        target = _at(now, offset, _guess_hour(candidates), minute)
        return target if target > now else _at(now, offset + 7, _guess_hour(candidates), minute)
    if rel_day == 0:
        future = [t for t in (_at(now, 0, h, minute) for h in candidates) if t > now]
        return min(future) if future else None
    if rel_day is not None:
        return _at(now, rel_day, _guess_hour(candidates), minute)
    future = [t for t in (_at(now, k, h, minute) for k in (0, 1) for h in candidates) if t > now]
    return min(future) if future else None


def _clock(rest: str) -> tuple[re.Match[str] | None, str]:
    match = _CLOCK_RE.search(rest)
    if match is None:
        return None, rest
    return match, (rest[: match.start()] + " " + rest[match.end() :]).strip()


def _clock_minute(match: re.Match[str]) -> int | None:
    if match.group(3) is not None:
        return 30
    minute = int(match.group(2)) if match.group(2) is not None else 0
    return minute if minute < 60 else None


def _duration(rest: str) -> tuple[int, str] | None:
    """문장에서 첫 지속 시간 표현을 찾아 (초, 그 표현을 뺀 나머지) 로 돌려준다."""
    for match in _DURATION_RE.finditer(rest):
        if not match.group().strip():
            continue
        hours, half, minutes, seconds = match.groups()
        total = int(hours or 0) * 3600 + (1800 if half else 0)
        total += int(minutes or 0) * 60 + int(seconds or 0)
        return total, (rest[: match.start()] + " " + rest[match.end() :]).strip()
    return None


def match_cancel(norm: NormalizedText, now: datetime) -> MatchResult | None:
    """알람/타이머 취소 발화를 해석한다. 취소 발화가 아니면 None."""
    clock_match, rest = _clock(norm.text)
    tokens = _classify(rest)
    if not (tokens.alarm_word or tokens.timer_word) or not tokens.cancel_verb:
        return None
    ok, label = _label(tokens)
    if not ok or tokens.rel_day is not None or tokens.recurring or tokens.weekdays or tokens.weekly:
        return NoMatch()
    if tokens.all_word:
        return Clarify(reason=ClarifyReason.BROAD_ACTION)
    if clock_match is None:
        # 시각 없이 "아침 알람 취소"처럼 시간대 단어만 있으면 라벨로 본다.
        label = label or (tokens.meridiem[0] if len(tokens.meridiem) == 1 else None)
        return CancelAlarmIntent(label=label, is_timer=tokens.timer_word)
    minute = _clock_minute(clock_match)
    meridiem = _MERIDIEM.get(tokens.meridiem[0]) if len(tokens.meridiem) == 1 else None
    candidates = _hour_candidates(int(clock_match.group(1)), meridiem)
    if minute is None or not candidates or len(tokens.meridiem) > 1 or tokens.timer_word:
        return NoMatch()
    return CancelAlarmIntent(
        label=label,
        hour=candidates[0] if len(candidates) == 1 else int(clock_match.group(1)),
        minute=minute,
        hour_ambiguous=len(candidates) > 1,
    )


def match_timer(norm: NormalizedText) -> MatchResult | None:
    """타이머 발화를 해석한다. 타이머 발화가 아니면 None."""
    clock_match, _ = _clock(norm.text)
    found = _duration(norm.text)
    tokens = _classify(found[1] if found else norm.text)
    relative = tokens.alarm_word or (tokens.rel_word and tokens.wake_verb)
    is_timer = tokens.timer_word or (found is not None and clock_match is None and relative)
    if not is_timer:
        return None
    if found is None or clock_match is not None or found[0] <= 0 or found[0] > _MAX_TIMER_SECONDS:
        return NoMatch()
    ok, label = _label(tokens)
    if not ok or tokens.cancel_verb or tokens.meridiem or tokens.rel_day is not None:
        return NoMatch()
    if tokens.recurring or tokens.weekdays or tokens.weekly or tokens.all_word:
        return NoMatch()
    if label is not None and not tokens.timer_word:
        return NoMatch()
    return TimerIntent(duration_seconds=found[0], label=label)


def match_alarm(norm: NormalizedText, now: datetime) -> MatchResult | None:
    """알람 생성 발화를 해석한다. 알람 발화가 아니면 None."""
    clock_match, rest = _clock(norm.text)
    tokens = _classify(rest)
    triggered = tokens.alarm_word or (clock_match is not None and tokens.wake_verb)
    if not triggered:
        return None
    if clock_match is None or tokens.cancel_verb or tokens.all_word or tokens.invalid:
        return NoMatch()
    ok, label = _label(tokens)
    if not ok or label is not None and not tokens.alarm_word or len(tokens.meridiem) > 1:
        return NoMatch()
    minute = _clock_minute(clock_match)
    meridiem = tokens.meridiem[0] if tokens.meridiem else None
    candidates = _hour_candidates(int(clock_match.group(1)), _MERIDIEM.get(meridiem or ""))
    if minute is None or not candidates:
        return NoMatch()
    return _build_alarm(tokens, candidates, minute, label, now)


def _build_alarm(
    tokens: _Tokens, candidates: tuple[int, ...], minute: int, label: str | None, now: datetime
) -> MatchResult:
    days = tokens.recurring
    if days is None and (tokens.weekly or len(tokens.weekdays) > 1):
        days = tuple(sorted(tokens.weekdays)) or None
    if days is not None and tokens.weekdays and tokens.recurring is not None:
        return NoMatch()
    if days is None and tokens.weekly:
        return NoMatch()
    if days is not None:
        hour = _repeat_hour(candidates)
        if hour is None or tokens.rel_day is not None:
            return NoMatch()
        return AlarmIntent(hour=hour, minute=minute, days=days, label=label)
    weekday = next(iter(tokens.weekdays)) if tokens.weekdays else None
    if weekday is not None and tokens.rel_day is not None:
        return NoMatch()
    target = _next_once(now, candidates, minute, tokens.rel_day, weekday)
    if target is None:
        return NoMatch()
    return AlarmIntent(
        hour=target.hour,
        minute=target.minute,
        one_shot_epoch_ms=int(target.timestamp() * 1000),
        label=label,
    )
