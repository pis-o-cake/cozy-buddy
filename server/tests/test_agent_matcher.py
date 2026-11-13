"""결정론 한국어 명령 매처 테스트. 표 기반으로 발화 -> 기대 결과를 검증한다."""

import random
from datetime import datetime

import pytest

from app.domain.agent.lexicon import STATIC_LEXICON
from app.domain.agent.matcher import match
from app.domain.agent.model import (
    AlarmIntent,
    CancelAlarmIntent,
    Clarify,
    ClarifyReason,
    CommandParams,
    DeviceAction,
    DeviceCommand,
    MatchResult,
    NoMatch,
    SceneCommand,
    StateQuery,
    TimerIntent,
    UtteranceContext,
    WeatherIntent,
)
from tests.agent_fixtures import BEDROOM, HOME, KIDS, KITCHEN, KST, LIVING, NOW

LIVING_CEILING = "light.living_ceiling"
LIVING_STAND = "light.living_stand"
BEDROOM_CEILING = "light.bedroom_ceiling"
BEDROOM_STAND = "light.bedroom_stand"
KITCHEN_CEILING = "light.kitchen_ceiling"
KETTLE = "switch.kitchen_kettle"
FAN = "fan.living_fan"
CURTAIN = "cover.bedroom_curtain"
KIDS_AC = "climate.kids_ac"
TEMP = "sensor.living_temp"
ALL_LIGHTS = (LIVING_CEILING, LIVING_STAND, BEDROOM_CEILING, BEDROOM_STAND, KITCHEN_CEILING)
ALL_SWITCHABLE = (*ALL_LIGHTS, KETTLE, FAN)

ON = DeviceAction.TURN_ON
OFF = DeviceAction.TURN_OFF
TOGGLE = DeviceAction.TOGGLE
SET_BRIGHTNESS = DeviceAction.SET_BRIGHTNESS
AMBIGUOUS = ClarifyReason.AMBIGUOUS_TARGET
BROAD = ClarifyReason.BROAD_ACTION


def cmd(action: DeviceAction, *ids: str, pct: int | None = None, delta: int | None = None):
    return DeviceCommand(ids, action, CommandParams(pct, delta))


def clarify(reason: ClarifyReason, *ids: str) -> Clarify:
    return Clarify(reason=reason, candidate_entity_ids=ids)


def ms(month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(2026, month, day, hour, minute, tzinfo=KST).timestamp() * 1000)


def run(text: str, hub: str | None = None, now: datetime = NOW) -> MatchResult:
    return match(UtteranceContext(text=text, hub_area_id=hub, now=now), HOME)


def assert_same(actual: MatchResult, expected: MatchResult) -> None:
    if isinstance(expected, Clarify):
        assert isinstance(actual, Clarify), actual
        assert actual.reason == expected.reason
        assert set(actual.candidate_entity_ids) == set(expected.candidate_entity_ids)
        return
    assert actual == expected


# (허브 방, 발화, 기대 결과)
EXPLICIT_ROOM = [
    (None, "거실 불 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (BEDROOM, "침실 불 꺼줘", cmd(OFF, BEDROOM_CEILING, BEDROOM_STAND)),
    (LIVING, "주방 불 꺼줘", cmd(OFF, KITCHEN_CEILING)),
    (None, "거실 조명 켜줘", cmd(ON, LIVING_CEILING)),
    (None, "거실 스탠드 켜줘", cmd(ON, LIVING_STAND)),
    (LIVING, "침실 스탠드 꺼줘", cmd(OFF, BEDROOM_STAND)),
    (None, "주방 전기포트 켜줘", cmd(ON, KETTLE)),
    (None, "거실 선풍기 틀어줘", cmd(ON, FAN)),
    (BEDROOM, "침실 커튼 열어줘", cmd(ON, CURTAIN)),
    (None, "침실 커튼 닫아줘", cmd(OFF, CURTAIN)),
    (None, "아이방 에어컨 켜줘", cmd(ON, KIDS_AC)),
    (None, "안방 조명 꺼줘", cmd(OFF, BEDROOM_CEILING)),
    (None, "거실에서 불 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (None, "거실의 불을 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (None, "거실불꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (None, "거실 조명 켜", cmd(ON, LIVING_CEILING)),
    (None, "침실 조명 토글해줘", cmd(TOGGLE, BEDROOM_CEILING)),
    (KITCHEN, "거실 선풍기 꺼줘", cmd(OFF, FAN)),
    (None, "거실하고 침실 조명 꺼줘", cmd(OFF, LIVING_CEILING, BEDROOM_CEILING)),
    (None, "거실이랑 침실 스탠드 켜줘", cmd(ON, LIVING_STAND, BEDROOM_STAND)),
]

COLLOQUIAL = [
    (LIVING, "불 좀 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 좀 꺼 줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 꺼주세요", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 꺼 주세요.", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 꺼줄래?", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 꺼 줄래요", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 꺼요", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 꺼!", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 꺼봐", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 끄자", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 켜라", cmd(ON, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불을 켜 주세요", cmd(ON, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "불 좀 켜주실래요?", cmd(ON, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "전등 켜줘", cmd(ON, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "라이트 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "선풍기 좀 틀어주세요", cmd(ON, FAN)),
    (LIVING, "선풍기 꺼 줘", cmd(OFF, FAN)),
    (BEDROOM, "커튼 좀 쳐줘", cmd(OFF, CURTAIN)),
    (BEDROOM, "커튼 걷어줘", cmd(ON, CURTAIN)),
    (BEDROOM, "커튼 내려줘", cmd(OFF, CURTAIN)),
    (BEDROOM, "블라인드 올려줘", cmd(ON, CURTAIN)),
    (KITCHEN, "콘센트 켜줘", cmd(ON, KETTLE)),
    (KITCHEN, "포트 좀 꺼줘", cmd(OFF, KETTLE)),
]

IMPLICIT_HUB = [
    (LIVING, "불 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (LIVING, "스탠드 켜줘", cmd(ON, LIVING_STAND)),
    (BEDROOM, "스탠드 켜줘", cmd(ON, BEDROOM_STAND)),
    (LIVING, "선풍기 꺼줘", cmd(OFF, FAN)),
    (LIVING, "조명 꺼줘", cmd(OFF, LIVING_CEILING)),
    (KITCHEN, "불 켜줘", cmd(ON, KITCHEN_CEILING)),
    (KITCHEN, "전기포트 켜줘", cmd(ON, KETTLE)),
    (LIVING, "전기포트 켜줘", cmd(ON, KETTLE)),
    (KIDS, "선풍기 켜줘", cmd(ON, FAN)),
    (BEDROOM, "커튼 닫아줘", cmd(OFF, CURTAIN)),
    (LIVING, "커튼 닫아줘", cmd(OFF, CURTAIN)),
    (KIDS, "에어컨 켜줘", cmd(ON, KIDS_AC)),
    (None, "에어컨 꺼줘", cmd(OFF, KIDS_AC)),
    (LIVING, "여기 불 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND)),
    (BEDROOM, "이 방 불 꺼줘", cmd(OFF, BEDROOM_CEILING, BEDROOM_STAND)),
    (KITCHEN, "방 불 꺼줘", cmd(OFF, KITCHEN_CEILING)),
    (BEDROOM, "스탠드 켜줘", cmd(ON, BEDROOM_STAND)),
]

AMBIGUITY = [
    (None, "스탠드 켜줘", clarify(AMBIGUOUS, LIVING_STAND, BEDROOM_STAND)),
    (KIDS, "스탠드 꺼줘", clarify(AMBIGUOUS, LIVING_STAND, BEDROOM_STAND)),
    (KITCHEN, "스탠드 켜줘", clarify(AMBIGUOUS, LIVING_STAND, BEDROOM_STAND)),
    (KIDS, "불 꺼줘", clarify(AMBIGUOUS, *ALL_LIGHTS)),
    (None, "조명 켜줘", clarify(AMBIGUOUS, LIVING_CEILING, BEDROOM_CEILING, KITCHEN_CEILING)),
    (KIDS, "조명 꺼줘", clarify(AMBIGUOUS, LIVING_CEILING, BEDROOM_CEILING, KITCHEN_CEILING)),
]

GLOBAL_ALL = [
    (LIVING, "전부 꺼줘", clarify(BROAD, *ALL_SWITCHABLE)),
    (None, "모든 불 꺼줘", clarify(BROAD, *ALL_LIGHTS)),
    (BEDROOM, "집 불 다 꺼줘", clarify(BROAD, *ALL_LIGHTS)),
    (None, "불 전부 꺼줘", clarify(BROAD, *ALL_LIGHTS)),
    (LIVING, "다 켜줘", clarify(BROAD, *ALL_SWITCHABLE)),
    (None, "집 전체 불 꺼줘", clarify(BROAD, *ALL_LIGHTS)),
    (None, "모든 조명 켜줘", clarify(BROAD, LIVING_CEILING, BEDROOM_CEILING, KITCHEN_CEILING)),
    (None, "모든 불 밝게 해줘", clarify(BROAD, *ALL_LIGHTS)),
]

AREA_ALL = [
    (None, "거실 전부 꺼줘", cmd(OFF, LIVING_CEILING, LIVING_STAND, FAN)),
    (LIVING, "주방 다 꺼줘", cmd(OFF, KITCHEN_CEILING, KETTLE)),
    (None, "침실 불 전부 꺼줘", cmd(OFF, BEDROOM_CEILING, BEDROOM_STAND)),
    (None, "거실 모든 불 켜줘", cmd(ON, LIVING_CEILING, LIVING_STAND)),
    (None, "거실 스탠드 다 켜줘", cmd(ON, LIVING_STAND)),
]

BRIGHTNESS = [
    (None, "거실 불 50%로 해줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, LIVING_STAND, pct=50)),
    (None, "거실 조명 밝기 70으로 맞춰줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, pct=70)),
    (None, "거실 조명 밝기 오십 퍼센트로 설정해줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, pct=50)),
    (LIVING, "스탠드 30퍼센트로", cmd(SET_BRIGHTNESS, LIVING_STAND, pct=30)),
    (None, "침실 불 밝게 해줘", cmd(SET_BRIGHTNESS, BEDROOM_CEILING, BEDROOM_STAND, delta=30)),
    (None, "거실 스탠드 어둡게 해줘", cmd(SET_BRIGHTNESS, LIVING_STAND, delta=-30)),
    (KITCHEN, "불 좀 밝게 해줘", cmd(SET_BRIGHTNESS, KITCHEN_CEILING, delta=30)),
    (BEDROOM, "조금 더 어둡게", cmd(SET_BRIGHTNESS, BEDROOM_CEILING, BEDROOM_STAND, delta=-30)),
    (None, "거실 조명 밝기 올려줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, delta=30)),
    (None, "거실 조명 밝기 낮춰줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, delta=-30)),
    (None, "거실 불 최대로 해줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, LIVING_STAND, pct=100)),
    (None, "거실 불 가장 밝게 해줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, LIVING_STAND, pct=100)),
    (None, "거실 불 가장 어둡게 해줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, LIVING_STAND, pct=10)),
    (None, "주방 불 50%로 켜줘", cmd(SET_BRIGHTNESS, KITCHEN_CEILING, pct=50)),
    (LIVING, "밝기 80으로 맞춰줘", cmd(SET_BRIGHTNESS, LIVING_CEILING, LIVING_STAND, pct=80)),
    (None, "거실 선풍기 밝게 해줘", NoMatch()),
    (None, "거실 불 150%로 해줘", NoMatch()),
    (None, "거실 불 0%로 해줘", NoMatch()),
    (None, "거실 불 올려줘", NoMatch()),
]

STATE_QUERY = [
    (None, "거실 불 켜져 있어?", StateQuery((LIVING_CEILING, LIVING_STAND))),
    (None, "거실 스탠드 켜져 있어", StateQuery((LIVING_STAND,))),
    (BEDROOM, "선풍기 켜져있어?", StateQuery((FAN,))),
    (None, "침실 커튼 열려 있어?", StateQuery((CURTAIN,))),
    (None, "침실 커튼 닫혀 있나요", StateQuery((CURTAIN,))),
    (None, "거실 온도 몇 도야", StateQuery((TEMP,))),
    (LIVING, "온도 알려줘", StateQuery((TEMP,))),
    (None, "주방 전기포트 꺼져 있어?", StateQuery((KETTLE,))),
    (KITCHEN, "불 꺼져 있나요", StateQuery((KITCHEN_CEILING,))),
    (None, "스탠드 켜져 있어?", StateQuery((LIVING_STAND, BEDROOM_STAND))),
    (LIVING, "불 상태 알려줘", StateQuery((LIVING_CEILING, LIVING_STAND))),
]

SCENES = [
    (None, "굿나잇 모드 켜줘", SceneCommand("scene.goodnight")),
    (None, "굿나잇 장면 실행해줘", SceneCommand("scene.goodnight")),
    (LIVING, "굿나잇 해줘", SceneCommand("scene.goodnight")),
    (None, "굿나잇 모드", SceneCommand("scene.goodnight")),
    (None, "외출 모드로 바꿔줘", SceneCommand("scene.away")),
    (None, "외출 장면 실행해줘", SceneCommand("scene.away")),
    (None, "외출 모드 켜줘", SceneCommand("scene.away")),
    (None, "굿나잇", NoMatch()),
    (None, "굿나잇 모드 꺼줘", NoMatch()),
    (None, "거실 굿나잇 모드 켜줘", NoMatch()),
]

DEVICE_TABLES = (
    EXPLICIT_ROOM,
    COLLOQUIAL,
    IMPLICIT_HUB,
    AMBIGUITY,
    GLOBAL_ALL,
    AREA_ALL,
    BRIGHTNESS,
    STATE_QUERY,
    SCENES,
)
DEVICE_CASES = [case for table in DEVICE_TABLES for case in table]


@pytest.mark.parametrize(("hub", "text", "expected"), DEVICE_CASES)
def test_device_utterances(hub: str | None, text: str, expected: MatchResult) -> None:
    assert_same(run(text, hub), expected)


ALARMS = [
    ("오전 7시 알람 맞춰줘", AlarmIntent(7, 0, one_shot_epoch_ms=ms(11, 14, 7))),
    ("7시 반 알람 맞춰줘", AlarmIntent(19, 30, one_shot_epoch_ms=ms(11, 13, 19, 30))),
    ("오전 7시 반 알람", AlarmIntent(7, 30, one_shot_epoch_ms=ms(11, 14, 7, 30))),
    ("내일 아침 6시 40분 알람 맞춰줘", AlarmIntent(6, 40, one_shot_epoch_ms=ms(11, 14, 6, 40))),
    ("내일 7시에 깨워줘", AlarmIntent(7, 0, one_shot_epoch_ms=ms(11, 14, 7))),
    ("모레 오전 9시 알람", AlarmIntent(9, 0, one_shot_epoch_ms=ms(11, 15, 9))),
    ("오늘 오후 6시에 알람 맞춰줘", AlarmIntent(18, 0, one_shot_epoch_ms=ms(11, 13, 18))),
    ("오늘 7시 알람", AlarmIntent(19, 0, one_shot_epoch_ms=ms(11, 13, 19))),
    ("저녁 7시 알람 맞춰줘", AlarmIntent(19, 0, one_shot_epoch_ms=ms(11, 13, 19))),
    ("새벽 5시 반에 깨워줘", AlarmIntent(5, 30, one_shot_epoch_ms=ms(11, 14, 5, 30))),
    ("월요일 7시 알람 맞춰줘", AlarmIntent(7, 0, one_shot_epoch_ms=ms(11, 16, 7))),
    ("평일 7시 알람 맞춰줘", AlarmIntent(7, 0, days=(1, 2, 3, 4, 5))),
    ("평일 아침 7시 반 알람", AlarmIntent(7, 30, days=(1, 2, 3, 4, 5))),
    ("매일 밤 11시 알람 맞춰줘", AlarmIntent(23, 0, days=(1, 2, 3, 4, 5, 6, 7))),
    ("매일 아침 6시 알람", AlarmIntent(6, 0, days=(1, 2, 3, 4, 5, 6, 7))),
    ("주말 9시 알람 맞춰줘", AlarmIntent(9, 0, days=(6, 7))),
    ("매주 월요일 8시 알람", AlarmIntent(8, 0, days=(1,))),
    ("화요일 목요일 오전 7시 알람 맞춰줘", AlarmIntent(7, 0, days=(2, 4))),
    ("일곱 시 반 알람 맞춰줘", AlarmIntent(19, 30, one_shot_epoch_ms=ms(11, 13, 19, 30))),
    ("내일 아침 여섯 시 사십 분 알람", AlarmIntent(6, 40, one_shot_epoch_ms=ms(11, 14, 6, 40))),
    ("내일 오전 7:30 알람", AlarmIntent(7, 30, one_shot_epoch_ms=ms(11, 14, 7, 30))),
    ("운동 알람 오전 6시 맞춰줘", AlarmIntent(6, 0, one_shot_epoch_ms=ms(11, 14, 6), label="운동")),
    ("평일 7시에 깨워줘", AlarmIntent(7, 0, days=(1, 2, 3, 4, 5))),
    ("평일 3시 알람", NoMatch()),
    ("25시 알람", NoMatch()),
    ("오전 13시 알람", NoMatch()),
    ("7시 90분 알람", NoMatch()),
    ("오늘 오전 7시 알람", NoMatch()),
    ("내일 평일 7시 알람", NoMatch()),
]

TIMERS = [
    ("3분 타이머", TimerIntent(180)),
    ("3분 타이머 맞춰줘", TimerIntent(180)),
    ("타이머 3분 해줘", TimerIntent(180)),
    ("1시간 반 타이머", TimerIntent(5400)),
    ("한 시간 반 타이머 맞춰줘", TimerIntent(5400)),
    ("30초 타이머", TimerIntent(30)),
    ("삼십 분 타이머 시작해줘", TimerIntent(1800)),
    ("2시간 타이머", TimerIntent(7200)),
    ("1시간 30분 타이머", TimerIntent(5400)),
    ("10분 뒤에 알려줘", TimerIntent(600)),
    ("5분 후에 깨워줘", TimerIntent(300)),
    ("3분짜리 타이머 맞춰줘", TimerIntent(180)),
    ("라면 타이머 3분", TimerIntent(180, label="라면")),
    ("타이머 맞춰줘", NoMatch()),
    ("0분 타이머", NoMatch()),
    ("25시간 타이머", NoMatch()),
    ("5분 후에 불 꺼줘", NoMatch()),
    ("0분 타이머 취소해줘", NoMatch()),
    ("25시간 타이머 취소해줘", NoMatch()),
    ("5분 알람 취소해줘", NoMatch()),
    ("내일 5분 타이머 취소해줘", NoMatch()),
    ("내일 7시 타이머 취소해줘", NoMatch()),
    ("내일 알람 취소해줘", NoMatch()),
    ("5분 타이머 5분 타이머 취소해줘", NoMatch()),
    ("매일 7시 알람 취소해줘", NoMatch()),
    ("월요일 7시 알람 취소해줘", NoMatch()),
    ("저녁 아침 7시 알람 취소해줘", NoMatch()),
]

CANCELS = [
    ("알람 취소해줘", CancelAlarmIntent()),
    ("알람 꺼줘", CancelAlarmIntent()),
    ("알람 삭제해줘", CancelAlarmIntent()),
    ("7시 알람 취소해줘", CancelAlarmIntent(hour=7, minute=0, hour_ambiguous=True)),
    ("오전 7시 알람 꺼줘", CancelAlarmIntent(hour=7, minute=0)),
    ("저녁 7시 반 알람 취소", CancelAlarmIntent(hour=19, minute=30)),
    ("운동 알람 삭제해줘", CancelAlarmIntent(label="운동")),
    ("타이머 취소해줘", CancelAlarmIntent(is_timer=True)),
    ("라면 타이머 꺼줘", CancelAlarmIntent(label="라면", is_timer=True)),
    ("5분 타이머 취소해줘", CancelAlarmIntent(is_timer=True, duration_seconds=300)),
    ("5분짜리 타이머 꺼줘", CancelAlarmIntent(is_timer=True, duration_seconds=300)),
    ("타이머 취소", CancelAlarmIntent(is_timer=True)),
    ("10분 타이머 삭제해줘", CancelAlarmIntent(is_timer=True, duration_seconds=600)),
    ("1시간 반 타이머 취소해줘", CancelAlarmIntent(is_timer=True, duration_seconds=5400)),
    ("30초 타이머 멈춰", CancelAlarmIntent(is_timer=True, duration_seconds=30)),
    ("라면 5분 타이머 꺼줘", CancelAlarmIntent(label="라면", is_timer=True, duration_seconds=300)),
    (
        "내일 아침 7시 알람 취소해줘",
        CancelAlarmIntent(hour=7, minute=0, day_offset=1),
    ),
    (
        "내일 7시 알람 취소해줘",
        CancelAlarmIntent(hour=7, minute=0, hour_ambiguous=True, day_offset=1),
    ),
    ("오늘 저녁 7시 반 알람 꺼줘", CancelAlarmIntent(hour=19, minute=30, day_offset=0)),
    ("7시 알람 지워줘", CancelAlarmIntent(hour=7, minute=0, hour_ambiguous=True)),
    ("운동 알람 취소", CancelAlarmIntent(label="운동")),
    ("모든 알람 삭제해줘", clarify(BROAD)),
    ("알람 전부 취소해줘", clarify(BROAD)),
]

SCHEDULE_CASES = [*ALARMS, *TIMERS, *CANCELS]


@pytest.mark.parametrize(("text", "expected"), SCHEDULE_CASES)
def test_schedule_utterances(text: str, expected: MatchResult) -> None:
    assert_same(run(text, LIVING), expected)


def test_ambiguous_hour_uses_next_future_occurrence() -> None:
    morning = datetime(2026, 11, 13, 6, 0, tzinfo=KST)
    assert run("7시 알람", now=morning) == AlarmIntent(7, 0, one_shot_epoch_ms=ms(11, 13, 7))
    evening = datetime(2026, 11, 13, 20, 0, tzinfo=KST)
    assert run("7시 알람", now=evening) == AlarmIntent(7, 0, one_shot_epoch_ms=ms(11, 14, 7))


NO_MATCH = [
    "농담 하나 해줘",
    "불고기 레시피 알려줘",
    "안녕",
    "지금 몇 시야",
    "너 이름이 뭐야",
    "노래 틀어줘",
    "고마워",
    "심심해",
    "배고파",
    "불 꺼진 것 같아",
    "등산 가고 싶어",
    "다음 주 일정 알려줘",
    "서울 인구가 몇 명이야",
    "전기요금 얼마야",
    "TV 켜줘",
    "커피 한 잔 주문해줘",
    "불 켜져 있을 때만 꺼줘",
    "7시에 불 꺼줘",
    "거실",
    "불",
    "꺼줘",
    "해줘",
    "알람 맞춰줘",
    "알람 소리 줄여줘",
    "거실 어때",
    "전부 알려줘",
    "거실 에어컨 켜줘",
    "아이방 불 꺼줘",
    "침실 선풍기 켜줘",
    "거실 불 열어줘",
    "커튼 켜줘",
    "아이방 다 꺼줘",
    "이 불 좀 어떻게 해봐",
    "무드 있는 조명 추천해줘",
    "불닭볶음면 끓이는 법",
    "다음 곡 재생해줘",
    "방 청소해줘",
    "꺼져",
    "",
    "   ",
    "?!",
]


@pytest.mark.parametrize("text", NO_MATCH)
def test_chitchat_is_no_match(text: str) -> None:
    assert run(text, LIVING) == NoMatch()


def test_substring_of_known_word_never_matches_light() -> None:
    for text in ("불고기 해줘", "등산 가자", "불안해", "다리 아파"):
        assert run(text, LIVING) == NoMatch()


# 어휘 조각을 하나도 포함하지 않는 단어들. 어떻게 섞어도 NoMatch 여야 한다.
_NEUTRAL_WORDS = [
    "오늘", "커피", "노래", "친구", "영화", "사과", "기분", "여행", "공부", "고양이",
    "주말에", "운동장", "점심", "책", "숙제", "음악", "병원", "택배", "비행기", "컴퓨터",
]  # fmt: skip


def test_random_non_command_sentences_are_no_match() -> None:
    rng = random.Random(20261113)
    for _ in range(300):
        words = rng.sample(_NEUTRAL_WORDS, rng.randint(1, 5))
        suffix = rng.choice(["", " 알려줘", " 해줘", "?", " 어때", " 좋아"])
        assert run(" ".join(words) + suffix, rng.choice([None, LIVING, KIDS])) == NoMatch()


def test_vocabulary_shuffles_never_raise() -> None:
    rng = random.Random(7)
    surfaces = [*STATIC_LEXICON, "거실", "침실", "7시", "30분", "50%", "알람", "타이머", "취소"]
    for _ in range(500):
        text = " ".join(rng.sample(surfaces, rng.randint(1, 6)))
        result = run(text, rng.choice([None, LIVING, BEDROOM, KITCHEN, KIDS]))
        assert isinstance(
            result,
            DeviceCommand
            | SceneCommand
            | StateQuery
            | AlarmIntent
            | TimerIntent
            | CancelAlarmIntent
            | WeatherIntent
            | Clarify
            | NoMatch,
        )


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UtteranceContext(text="불 꺼줘", hub_area_id=None, now=datetime(2026, 11, 13, 9, 0))
