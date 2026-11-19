"""Turn 객체 단위 테스트: 단계 시간 누적, 첫 오디오 시각, 스킬 결과 요약."""

from app.domain.telemetry.turn import MAX_RESPONSE_CHARS, SkillOutcome, Stage, Turn, TurnSource


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_turn(clock: FakeClock) -> Turn:
    return Turn("hub-1", TurnSource.VOICE, started_at=clock.now, clock=clock)


def test_timed_accumulates_across_blocks_and_on_error() -> None:
    clock = FakeClock()
    turn = make_turn(clock)

    with turn.timed(Stage.UNDERSTAND):
        clock.now += 0.25
    try:
        with turn.timed(Stage.UNDERSTAND):
            clock.now += 0.5
            raise ValueError("boom")
    except ValueError:
        pass

    assert turn.t_understand_ms == 750
    assert turn.t_act_ms is None


def test_first_audio_is_relative_to_start_and_set_once() -> None:
    clock = FakeClock()
    turn = make_turn(clock)

    turn.mark_first_audio(101.2)
    turn.mark_first_audio(105.0)

    assert turn.t_first_audio_ms == 1200


def test_skill_result_failure_is_sticky() -> None:
    turn = make_turn(FakeClock())
    assert turn.skill_result is SkillOutcome.NONE

    turn.record_skill(True)
    assert turn.skill_result is SkillOutcome.OK
    turn.record_skill(False)
    turn.record_skill(True)

    assert turn.skill_result is SkillOutcome.FAILED


def test_response_is_capped_and_first_error_wins() -> None:
    turn = make_turn(FakeClock())

    turn.append_response("가" * (MAX_RESPONSE_CHARS + 50))
    turn.append_response("더")
    turn.fail("first")
    turn.fail("second")

    assert len(turn.response_text) == MAX_RESPONSE_CHARS
    assert turn.error_code == "first"
