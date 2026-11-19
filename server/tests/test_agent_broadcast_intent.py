"""방송 의도 매처 테스트. 발화 -> (대상, 방송 문장) 표 기반."""

import pytest

from app.domain.agent.broadcast_intent import match_broadcast
from app.domain.agent.matcher import match
from app.domain.agent.model import BroadcastIntent, NoMatch, UtteranceContext
from app.domain.agent.normalize import normalize
from tests.agent_fixtures import HOME, NOW

POSITIVE = [
    ("주방에 밥 먹으라고 방송해줘", "주방", "밥 먹으세요"),
    ("주방에 밥 먹으라고 방송해 줘", "주방", "밥 먹으세요"),
    ("주방에 밥 먹으라고 방송 좀 해줘", "주방", "밥 먹으세요"),
    ("주방에 밥 먹으라고 방송해주세요", "주방", "밥 먹으세요"),
    ("주방에 밥 먹으라고 방송해", "주방", "밥 먹으세요"),
    ("주방에게 밥 먹으라고 방송해줘", "주방", "밥 먹으세요"),
    ("밥 먹으라고 주방에 방송해줘", "주방", "밥 먹으세요"),
    ("침실에 이제 자라고 방송해줘", "침실", "이제 자세요"),
    ("아이방에 숙제 하라고 방송해줘", "아이방", "숙제 하세요"),
    ("거실에 저녁 먹자고 방송해줘", "거실", "저녁 먹자"),
    ("다들 밥 먹으라고 방송해줘", None, "다들 밥 먹으세요"),
    ("밥 먹으라고 방송해줘", None, "밥 먹으세요"),
    ("모두에게 밥 먹으라고 방송해줘", None, "밥 먹으세요"),
    ("집 전체에 청소하라고 방송해줘", None, "청소하세요"),
    ("모든 허브에 빨리 나오라고 방송해줘", None, "나오세요"),
    ("주방에 손님 오셨어요 방송해줘", "주방", "손님 오셨어요"),
]

NEGATIVE = [
    "뉴스 방송해줘",
    "축구 중계 방송 켜줘",
    "주방에 방송해줘",
    "방송해줘",
    "방송 좀 해줘 밥 먹어",
    "거실 불 꺼줘",
    "방송 볼륨 줄여줘",
    "내일 3시에 병원 가라고 알려줘",
    "알람 맞춰줘",
    "오늘 날씨 어때",
]


@pytest.mark.parametrize(("text", "target", "message"), POSITIVE)
def test_broadcast_phrasings(text: str, target: str | None, message: str) -> None:
    assert match_broadcast(normalize(text)) == BroadcastIntent(target, message)


@pytest.mark.parametrize("text", NEGATIVE)
def test_non_broadcast_phrasings_do_not_match(text: str) -> None:
    assert match_broadcast(normalize(text)) is None


def test_time_word_before_particle_is_not_a_target() -> None:
    intent = match_broadcast(normalize("저녁에 밥 먹으라고 방송해줘"))

    assert intent == BroadcastIntent(None, "저녁에 밥 먹으세요")


def test_matcher_entry_returns_broadcast_intent() -> None:
    context = UtteranceContext("주방에 밥 먹으라고 방송해줘", "living", NOW)

    assert match(context, HOME) == BroadcastIntent("주방", "밥 먹으세요")


def test_matcher_entry_leaves_device_commands_alone() -> None:
    context = UtteranceContext("거실 조명 꺼줘", "living", NOW)

    result = match(context, HOME)

    assert not isinstance(result, BroadcastIntent | NoMatch)
