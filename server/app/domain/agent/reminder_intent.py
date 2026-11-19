"""리마인더 발화("내일 3시에 병원 가라고 알려줘")를 ReminderIntent 로 해석한다.

순수 함수만 둔다.
"""

import re
from datetime import datetime, timedelta

from app.domain.agent.model import AlarmIntent, NoMatch, ReminderIntent
from app.domain.agent.normalize import NormalizedText
from app.domain.agent.schedule import (
    _CLOCK_RE,
    _DURATION_RE,
    _MAX_TIMER_SECONDS,
    _MERIDIEM,
    _build_alarm,
    _classify,
    _clock_minute,
    _hour_candidates,
)

__all__ = ["LABEL_SUFFIX", "match_reminder"]

# normalize() 가 요청 어미를 뗀 뒤의 꼴이다. 알려줘->알려, 말해줘->말, 리마인드해줘->리마인드.
_REMINDER_RE = re.compile(r"^(?P<head>.+?)\s*(?:으?라고)\s*(?:알려|말|리마인드|리마인더)$")
_REL_WORDS = frozenset({"뒤", "후", "이따", "이따가", "있다가"})
_REL_FORMS = _REL_WORDS | {f"{word}에" for word in _REL_WORDS}
_HANGUL_END_RE = re.compile(r"[가-힣]$")
_BLOCKED_CONTENT_WORDS = ("알람", "모닝콜", "타이머", "취소", "깨워", "알려", "말해", "리마인드")
_MAX_CONTENT_WORDS = 5
_MAX_CONTENT_CHARS = 40
LABEL_SUFFIX = "기"


def match_reminder(norm: NormalizedText, now: datetime) -> ReminderIntent | NoMatch | None:
    """리마인더 발화를 해석한다.

    시각(또는 "N분 뒤")이 앞에 오고, "~라고/~하라고/~으라고 알려/말해/리마인드" 앞부분이 내용이다.
    시각이 있는데 내용이나 시각을 확신 있게 해석하지 못하면 NoMatch 를 돌려 LLM 에 맡긴다.
    시각이 없거나 "라고 알려" 꼴이 아니면 None 을 돌려 알람·타이머 등 다른 매처가 보게 한다.

    Args:
        norm: 정규화된 발화.
        now: 시간대가 있는 현재 시각.

    Returns:
        ReminderIntent. label 은 "병원 가기" 처럼 내용 + "기"다. 리마인더 꼴이 아니면 None.
    """
    found = _REMINDER_RE.match(norm.text)
    if found is None:
        return None
    head = found.group("head")
    if (clock := _CLOCK_RE.search(head)) is not None:
        return _at_clock(head, clock, now) or NoMatch()
    duration = _DURATION_RE.match(head)
    if duration is None or not duration.group().strip():
        return None
    return _after_duration(head, now) or NoMatch()


def _label(content: str) -> str | None:
    words = content.split()
    if not 0 < len(words) <= _MAX_CONTENT_WORDS or len(content) > _MAX_CONTENT_CHARS:
        return None
    if not _HANGUL_END_RE.search(content) or _CLOCK_RE.search(content):
        return None
    if any(word in content for word in _BLOCKED_CONTENT_WORDS):
        return None
    return content + LABEL_SUFFIX


def _at_clock(head: str, clock: re.Match[str], now: datetime) -> ReminderIntent | None:
    label = _label(head[clock.end() :].strip())
    if label is None:
        return None
    tokens = _classify(head[: clock.start()].strip())
    blocked = (
        tokens.leftovers
        or tokens.alarm_word
        or tokens.timer_word
        or tokens.wake_verb
        or tokens.rel_word
        or tokens.cancel_verb
        or tokens.all_word
        or tokens.invalid
        or len(tokens.meridiem) > 1
    )
    minute = _clock_minute(clock)
    if blocked or minute is None:
        return None
    meridiem = tokens.meridiem[0] if tokens.meridiem else None
    candidates = _hour_candidates(int(clock.group(1)), _MERIDIEM.get(meridiem or ""))
    if not candidates:
        return None
    built = _build_alarm(tokens, candidates, minute, None, now)
    if not isinstance(built, AlarmIntent):
        return None
    return ReminderIntent(
        label, built.hour, built.minute, days=built.days, one_shot_epoch_ms=built.one_shot_epoch_ms
    )


def _after_duration(head: str, now: datetime) -> ReminderIntent | None:
    duration = _DURATION_RE.match(head)
    if duration is None or not duration.group().strip():
        return None
    hours, half, minutes, seconds = duration.groups()
    total = int(hours or 0) * 3600 + (1800 if half else 0) + int(minutes or 0) * 60
    total += int(seconds or 0)
    if not 0 < total <= _MAX_TIMER_SECONDS:
        return None
    words = head[duration.end() :].split()
    if not words or words[0] not in _REL_FORMS:
        return None
    label = _label(" ".join(words[1:]))
    if label is None:
        return None
    target = now + timedelta(seconds=total)
    return ReminderIntent(
        label, target.hour, target.minute, one_shot_epoch_ms=int(target.timestamp() * 1000)
    )
