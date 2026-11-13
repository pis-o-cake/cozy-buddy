"""정규화와 템플릿 응답 테스트."""

import json
from pathlib import Path

import pytest

from app.core.i18n import Translator
from app.domain.agent.matcher import match
from app.domain.agent.model import UtteranceContext
from app.domain.agent.normalize import entity_base_name, normalize
from app.domain.agent.responses import build_response, build_state_response, josa, render
from tests.agent_fixtures import HOME, LIVING, NOW

translate = Translator("ko")


@pytest.mark.parametrize(
    ("raw", "text", "requested"),
    [
        ("불 꺼줘", "불 꺼", True),
        ("불 좀 꺼 줘요!", "불 꺼", True),
        ("조명 켜주세요.", "조명 켜", True),
        ("밝게 해줘", "밝게", True),
        ("굿나잇 해", "굿나잇", True),
        ("불 꺼요", "불 꺼", False),
        ("  거실   불,  켜  ", "거실 불 켜", False),
        ("일곱 시 반에 깨워줘", "7시 반에 깨워", True),
        ("한 시간 반 타이머", "1시간 반 타이머", False),
        ("열두 시 삼십 분", "12시 30분", False),
        ("오십 퍼센트로", "50%로", False),
        ("백 프로", "100%", False),
        ("7:30 알람", "7시 30분 알람", False),
        ("7:00 알람", "7시 알람", False),
    ],
)
def test_normalize(raw: str, text: str, requested: bool) -> None:
    result = normalize(raw)
    assert result.text == text
    assert result.requested is requested
    assert result.compact == text.replace(" ", "")


def test_entity_base_name_strips_area_prefix() -> None:
    assert entity_base_name("거실 조명", "거실") == "조명"
    assert entity_base_name("스탠드", "거실") == "스탠드"
    assert entity_base_name("거실", "거실") == "거실"
    assert entity_base_name("조명", None) == "조명"


@pytest.mark.parametrize(
    ("word", "marker", "expected"),
    [
        ("조명", "을(를)", "을"),
        ("선풍기", "을(를)", "를"),
        ("스탠드", "이(가)", "가"),
        ("전기포트", "은(는)", "는"),
        ("방", "이(가)", "이"),
        ("거실", "으로(로)", "로"),
        ("침실", "으로(로)", "로"),
        ("주방", "으로(로)", "으로"),
        ("28도", "(이)에요", "예요"),
        ("50%", "(이)에요", "예요"),
        ("3", "을(를)", "을"),
        ("2", "을(를)", "를"),
    ],
)
def test_josa(word: str, marker: str, expected: str) -> None:
    assert josa(word, marker) == expected


def respond(text: str, hub: str | None = LIVING) -> str:
    result = match(UtteranceContext(text, hub, NOW), HOME)
    response = build_response(result, HOME, NOW, translate)
    assert response is not None, result
    return render(response, translate)


@pytest.mark.parametrize(
    ("text", "hub", "expected"),
    [
        ("거실 조명 꺼줘", None, "거실 조명을 껐어요"),
        ("거실 스탠드 켜줘", None, "거실 스탠드를 켰어요"),
        ("거실 선풍기 켜줘", None, "거실 선풍기를 켰어요"),
        ("주방 전기포트 켜줘", None, "주방 전기포트를 켰어요"),
        ("거실 불 꺼줘", None, "거실 조명 2개를 껐어요"),
        ("침실 커튼 열어줘", None, "침실 커튼을 열었어요"),
        ("침실 커튼 닫아줘", None, "침실 커튼을 닫았어요"),
        ("거실 조명 밝기 50으로 맞춰줘", None, "거실 조명 밝기를 50%로 맞췄어요"),
        ("거실 스탠드 밝게 해줘", None, "거실 스탠드를 더 밝게 했어요"),
        ("굿나잇 모드 켜줘", None, "굿나잇 장면을 실행했어요"),
        ("스탠드 켜줘", None, "거실 스탠드, 침실 스탠드 중에 어떤 건가요?"),
        ("전부 꺼줘", None, "한꺼번에 기기 7개에 적용돼요. 정말 진행할까요?"),
        ("오전 7시 알람 맞춰줘", None, "내일 오전 7시에 알람을 맞췄어요"),
        ("오늘 오후 6시 30분에 알람", None, "오늘 오후 6시 반에 알람을 맞췄어요"),
        ("내일 오전 6시 40분 알람", None, "내일 오전 6시 40분에 알람을 맞췄어요"),
        ("모레 오전 9시 알람", None, "모레 오전 9시에 알람을 맞췄어요"),
        ("평일 7시 알람", None, "평일 오전 7시에 울리는 알람을 맞췄어요"),
        ("매일 밤 11시 알람", None, "매일 오후 11시에 울리는 알람을 맞췄어요"),
        ("화요일 목요일 오전 7시 알람", None, "화요일, 목요일 오전 7시에 울리는 알람을 맞췄어요"),
        ("운동 알람 오전 6시", None, "내일 오전 6시에 운동 알람을 맞췄어요"),
        ("1시간 반 타이머", None, "1시간 30분 타이머를 시작할게요"),
        ("30초 타이머", None, "30초 타이머를 시작할게요"),
        ("라면 타이머 3분", None, "라면 3분 타이머를 시작할게요"),
        ("알람 취소해줘", None, "알람을 취소했어요"),
        ("7시 알람 취소해줘", None, "7시 알람을 취소했어요"),
        ("타이머 취소해줘", None, "타이머를 취소했어요"),
        ("모든 알람 삭제해줘", None, "알람이나 타이머를 모두 취소할까요?"),
    ],
)
def test_render_responses(text: str, hub: str | None, expected: str) -> None:
    assert respond(text, hub) == expected


def test_many_device_response_uses_domain_noun() -> None:
    assert respond("거실 전부 꺼줘") == "거실 기기 3개를 껐어요"


def test_no_match_has_no_response() -> None:
    result = match(UtteranceContext("오늘 날씨 어때", LIVING, NOW), HOME)
    assert build_response(result, HOME, NOW, translate) is None


@pytest.mark.parametrize(
    ("entity_id", "state", "expected"),
    [
        ("light.living_ceiling", "on", "거실 조명이 켜져 있어요"),
        ("light.living_stand", "off", "거실 스탠드가 꺼져 있어요"),
        ("cover.bedroom_curtain", "open", "침실 커튼이 열려 있어요"),
        ("cover.bedroom_curtain", "closed", "침실 커튼이 닫혀 있어요"),
        ("sensor.living_temp", "28도", "거실 온도는 28도예요"),
        ("sensor.living_temp", "45%", "거실 온도는 45%예요"),
    ],
)
def test_state_response(entity_id: str, state: str, expected: str) -> None:
    response = build_state_response(HOME, entity_id, state)
    assert response is not None
    assert render(response, translate) == expected


def test_state_response_unknown_entity() -> None:
    assert build_state_response(HOME, "light.missing", "on") is None


def test_all_template_keys_exist_in_locale() -> None:
    locale = json.loads(
        (Path(__file__).resolve().parent.parent / "app" / "locales" / "ko.json").read_text(
            encoding="utf-8"
        )
    )
    verbs = ["on", "off", "toggle", "open", "close", "brightness_set", "brighter", "darker"]
    keys = [f"agent.device.{v}{s}" for v in verbs for s in ("", "_many")]
    assert all(key in locale for key in keys)
