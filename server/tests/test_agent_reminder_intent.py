"""리마인더 발화 해석 테스트. 알람·타이머와 겹치는 발화가 기존 동작을 유지하는지도 확인한다."""

from datetime import datetime, timedelta

import pytest

from app.domain.agent.matcher import match
from app.domain.agent.model import (
    AlarmIntent,
    MatchResult,
    NoMatch,
    ReminderIntent,
    TimerIntent,
    UtteranceContext,
)
from tests.agent_fixtures import HOME, KST, NOW

EVERY_DAY = (1, 2, 3, 4, 5, 6, 7)
WEEKDAYS = (1, 2, 3, 4, 5)


def ms(month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(2026, month, day, hour, minute, tzinfo=KST).timestamp() * 1000)


def relative(label: str, delta: timedelta) -> ReminderIntent:
    target = NOW + delta
    return ReminderIntent(
        label, target.hour, target.minute, one_shot_epoch_ms=int(target.timestamp() * 1000)
    )


def run(text: str) -> MatchResult:
    return match(UtteranceContext(text, None, NOW), HOME)


POSITIVE: list[tuple[str, ReminderIntent]] = [
    (
        "내일 3시에 병원 가라고 알려줘",
        ReminderIntent("병원 가기", 15, 0, one_shot_epoch_ms=ms(11, 14, 15)),
    ),
    (
        "내일 오후 3시에 병원 가라고 알려줘",
        ReminderIntent("병원 가기", 15, 0, one_shot_epoch_ms=ms(11, 14, 15)),
    ),
    (
        "내일 오전 10시 30분에 치과 가라고 알려줘",
        ReminderIntent("치과 가기", 10, 30, one_shot_epoch_ms=ms(11, 14, 10, 30)),
    ),
    ("30분 뒤에 빨래 꺼내라고 알려줘", relative("빨래 꺼내기", timedelta(minutes=30))),
    ("10분 후에 불 끄라고 알려줘", relative("불 끄기", timedelta(minutes=10))),
    ("한 시간 뒤에 전화하라고 알려줘", relative("전화하기", timedelta(hours=1))),
    ("1시간 반 뒤에 운동하라고 알려줘", relative("운동하기", timedelta(hours=1, minutes=30))),
    ("매일 아침 8시에 약 먹으라고 알려줘", ReminderIntent("약 먹기", 8, 0, days=EVERY_DAY)),
    (
        "평일 7시 30분에 출근하라고 리마인드 해줘",
        ReminderIntent("출근하기", 7, 30, days=WEEKDAYS),
    ),
    ("매일 밤 10시에 문 잠그라고 말해줘", ReminderIntent("문 잠그기", 22, 0, days=EVERY_DAY)),
    (
        "오늘 오후 6시에 아이 데리러 가라고 알려줘",
        ReminderIntent("아이 데리러 가기", 18, 0, one_shot_epoch_ms=ms(11, 13, 18)),
    ),
    (
        "모레 아침 9시에 택배 받으라고 알려줘",
        ReminderIntent("택배 받기", 9, 0, one_shot_epoch_ms=ms(11, 15, 9)),
    ),
    (
        "저녁 7시에 약 먹으라고 알려 줄래",
        ReminderIntent("약 먹기", 19, 0, one_shot_epoch_ms=ms(11, 13, 19)),
    ),
    (
        "내일 세 시에 병원 가라고 알려주세요",
        ReminderIntent("병원 가기", 15, 0, one_shot_epoch_ms=ms(11, 14, 15)),
    ),
    (
        "주말 아침 9시에 화분에 물 주라고 알려줘",
        ReminderIntent("화분에 물 주기", 9, 0, days=(6, 7)),
    ),
]

# 리마인더가 아닌 발화는 기존 알람·타이머 해석이나 NoMatch 를 그대로 유지해야 한다.
STAY_OTHER: list[tuple[str, type]] = [
    ("7시에 깨워줘", AlarmIntent),
    ("내일 7시에 알람 맞춰줘", AlarmIntent),
    ("매일 아침 6시 알람", AlarmIntent),
    ("30분 뒤에 알려줘", TimerIntent),
    ("10분 타이머 맞춰줘", TimerIntent),
    ("병원 가라고 알려줘", NoMatch),
    ("내일 병원 가라고 알려줘", NoMatch),
    ("3시에 알람 취소하라고 알려줘", NoMatch),
    ("30분 동안 청소하라고 알려줘", NoMatch),
    ("5분 뒤에 알려줘라고 알려줘", NoMatch),
]


@pytest.mark.parametrize(("text", "expected"), POSITIVE, ids=[t for t, _ in POSITIVE])
def test_reminder_is_understood(text: str, expected: ReminderIntent) -> None:
    assert run(text) == expected


@pytest.mark.parametrize(("text", "kind"), STAY_OTHER, ids=[t for t, _ in STAY_OTHER])
def test_non_reminders_keep_their_existing_result(text: str, kind: type) -> None:
    assert isinstance(run(text), kind)


def test_ringing_control_still_wins_while_ringing() -> None:
    result = match(UtteranceContext("알람 꺼줘", None, NOW, ringing=True), HOME)

    assert not isinstance(result, ReminderIntent)
