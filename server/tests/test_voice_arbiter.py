"""다중 허브 중재기 단위 테스트: 유사도, 창, 순서, 보류와 그 지연."""

import asyncio
import time

import pytest

from app.domain.voice.arbiter import UtteranceArbiter, is_same_text, normalize, similarity

HOLD = 0.1


@pytest.fixture
def arbiter() -> UtteranceArbiter:
    return UtteranceArbiter(hold_seconds=HOLD)


def test_normalize_drops_spacing_and_punctuation() -> None:
    assert normalize("  거실 조명, 꺼줘! ") == "거실조명꺼줘"
    assert normalize("Turn ON") == "turnon"


@pytest.mark.parametrize(
    ("a", "b", "expected_at_least"),
    [
        ("거실 조명 꺼줘", "거실 조명 꺼줘.", 1.0),
        ("거실 조명 꺼줘", "거실 조명 꺼줘요", 0.85),
        ("오늘 날씨 어때", "오늘 날씨 어때요", 0.85),
    ],
)
def test_similar_sentences_pass_threshold(a: str, b: str, expected_at_least: float) -> None:
    assert similarity(a, b) >= expected_at_least


def test_short_sentences_must_match_exactly() -> None:
    # 한 글자 차이라 비율은 0.8 을 넘지만 서로 다른 명령이다.
    assert similarity("거실 조명 꺼줘", "침실 조명 꺼줘") >= 0.8
    assert not is_same_text("거실 조명 꺼줘", "침실 조명 꺼줘")
    assert is_same_text("거실 조명 꺼줘", "거실 조명, 꺼줘.")


def test_long_sentences_tolerate_small_stt_differences() -> None:
    assert is_same_text("내일 아침 일곱 시에 알람 맞춰줘", "내일 아침 7 시에 알람 맞춰줘")
    assert not is_same_text("내일 아침 일곱 시에 알람 맞춰줘", "오늘 저녁 메뉴 뭐가 좋을까")


def test_empty_text_is_never_a_duplicate() -> None:
    assert similarity("", "") == 0.0
    assert not is_same_text("", "")


async def test_single_hub_never_waits(arbiter: UtteranceArbiter) -> None:
    ticket = arbiter.begin("hub-1", 10.0)
    began = time.monotonic()
    decision = await arbiter.decide(ticket, "거실 조명 꺼줘")
    assert decision.winner
    assert time.monotonic() - began < 0.02


async def test_earlier_utterance_wins_and_is_never_delayed(arbiter: UtteranceArbiter) -> None:
    early = arbiter.begin("hub-1", 10.0)
    late = arbiter.begin("hub-2", 10.2)

    began = time.monotonic()
    first = await arbiter.decide(early, "거실 조명 꺼줘")
    early_delay = time.monotonic() - began
    second = await arbiter.decide(late, "거실 조명 꺼줘")

    assert first.winner
    assert early_delay < 0.02
    assert not second.winner
    assert second.winner_hub_id == "hub-1"


async def test_utterances_outside_window_are_both_answered(arbiter: UtteranceArbiter) -> None:
    first = arbiter.begin("hub-1", 10.0)
    second = arbiter.begin("hub-2", 11.3)

    assert (await arbiter.decide(first, "거실 조명 꺼줘")).winner
    assert (await arbiter.decide(second, "거실 조명 꺼줘")).winner


async def test_utterance_just_inside_window_is_declined(arbiter: UtteranceArbiter) -> None:
    first = arbiter.begin("hub-1", 10.0)
    second = arbiter.begin("hub-2", 11.19)

    assert (await arbiter.decide(first, "거실 조명 꺼줘")).winner
    assert not (await arbiter.decide(second, "거실 조명 꺼줘")).winner


async def test_different_sentences_are_both_answered(arbiter: UtteranceArbiter) -> None:
    first = arbiter.begin("hub-1", 10.0)
    second = arbiter.begin("hub-2", 10.1)

    assert (await arbiter.decide(first, "거실 조명 꺼줘")).winner
    assert (await arbiter.decide(second, "침실 조명 꺼줘")).winner


async def test_same_hub_is_never_its_own_duplicate(arbiter: UtteranceArbiter) -> None:
    first = arbiter.begin("hub-1", 10.0)
    second = arbiter.begin("hub-1", 10.5)

    assert (await arbiter.decide(first, "거실 조명 꺼줘")).winner
    assert (await arbiter.decide(second, "거실 조명 꺼줘")).winner


async def test_homes_are_independent(arbiter: UtteranceArbiter) -> None:
    first = arbiter.begin("hub-1", 10.0, home_id="home-a")
    second = arbiter.begin("hub-2", 10.1, home_id="home-b")

    assert (await arbiter.decide(first, "거실 조명 꺼줘")).winner
    assert (await arbiter.decide(second, "거실 조명 꺼줘")).winner


async def test_later_one_holds_for_earlier_duplicate_and_loses(arbiter: UtteranceArbiter) -> None:
    early = arbiter.begin("hub-1", 10.0)
    late = arbiter.begin("hub-2", 10.2)

    async def early_stt_finishes_late() -> None:
        await asyncio.sleep(0.04)
        assert (await arbiter.decide(early, "거실 조명 꺼줘")).winner

    task = asyncio.create_task(early_stt_finishes_late())
    began = time.monotonic()
    decision = await arbiter.decide(late, "거실 조명 꺼줘")
    held = time.monotonic() - began
    await task

    assert not decision.winner
    assert decision.winner_hub_id == "hub-1"
    # 보류는 이른 쪽 STT 가 끝나는 시점(약 40ms)에서 풀리고 HOLD 를 넘기지 않는다.
    assert 0.03 <= held < HOLD


async def test_hold_is_bounded_when_earlier_never_finishes(arbiter: UtteranceArbiter) -> None:
    arbiter.begin("hub-1", 10.0)
    late = arbiter.begin("hub-2", 10.2)

    began = time.monotonic()
    decision = await arbiter.decide(late, "거실 조명 꺼줘")
    held = time.monotonic() - began

    assert decision.winner
    assert HOLD * 0.9 <= held < HOLD + 0.1


async def test_hold_ends_early_when_earlier_has_different_sentence(
    arbiter: UtteranceArbiter,
) -> None:
    early = arbiter.begin("hub-1", 10.0)
    late = arbiter.begin("hub-2", 10.2)

    async def early_decides() -> None:
        await asyncio.sleep(0.03)
        await arbiter.decide(early, "침실 조명 꺼줘")

    task = asyncio.create_task(early_decides())
    began = time.monotonic()
    decision = await arbiter.decide(late, "거실 조명 꺼줘")
    held = time.monotonic() - began
    await task

    assert decision.winner
    assert held < HOLD * 0.8


async def test_abandoned_earlier_releases_the_hold_immediately(arbiter: UtteranceArbiter) -> None:
    early = arbiter.begin("hub-1", 10.0)
    late = arbiter.begin("hub-2", 10.2)

    asyncio.get_running_loop().call_later(0.02, arbiter.abandon, early)
    began = time.monotonic()
    decision = await arbiter.decide(late, "거실 조명 꺼줘")

    assert decision.winner
    assert time.monotonic() - began < HOLD * 0.8


async def test_earlier_arriving_after_hold_is_declined(arbiter: UtteranceArbiter) -> None:
    early = arbiter.begin("hub-1", 10.0)
    late = arbiter.begin("hub-2", 10.2)

    assert (await arbiter.decide(late, "거실 조명 꺼줘")).winner
    decision = await arbiter.decide(early, "거실 조명 꺼줘")

    assert not decision.winner
    assert decision.winner_hub_id == "hub-2"


async def test_earlier_in_flight_does_not_hold_when_peer_ended_later(
    arbiter: UtteranceArbiter,
) -> None:
    early = arbiter.begin("hub-1", 10.0)
    arbiter.begin("hub-2", 10.2)

    began = time.monotonic()
    decision = await arbiter.decide(early, "거실 조명 꺼줘")

    assert decision.winner
    assert time.monotonic() - began < 0.02


async def test_old_records_are_pruned() -> None:
    now = [0.0]
    arbiter = UtteranceArbiter(clock=lambda: now[0])
    first = arbiter.begin("hub-1", 10.0)
    await arbiter.decide(first, "거실 조명 꺼줘")

    now[0] = 60.0
    arbiter.begin("hub-2", 11.0)

    assert len(arbiter._tickets[""]) == 1  # noqa: SLF001


def test_utterances_differing_in_numbers_are_not_duplicates() -> None:
    assert not is_same_text("거실 조명 20퍼센트로 맞춰줘", "거실 조명 80퍼센트로 맞춰줘")
    assert not is_same_text("거실 조명 이십 퍼센트로 맞춰줘", "거실 조명 팔십 퍼센트로 맞춰줘")
    assert is_same_text("거실 조명 20퍼센트로 맞춰줘", "거실 조명 20퍼센트로, 맞춰줘.")
