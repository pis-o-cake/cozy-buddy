"""울리는 중에는 알람 제어가 후속 지시(Dismiss)보다 먼저 해석된다."""

import pytest

from app.domain.agent.alarm_control_attempt import AlarmControlAttempt
from app.domain.agent.memory import ConversationMemory, TargetMemory
from app.domain.agent.model import (
    AlarmControlIntent,
    AlarmControlKind,
    CommandParams,
    DeviceAction,
    DeviceCommand,
    Dismiss,
)
from app.domain.agent.understand.context import ContextAttempt
from app.domain.agent.understand.followup import FollowupResolver
from app.domain.agent.understand.steps import MatcherAttempt
from app.domain.agent.understand.understander import Step, Understander
from tests.agent_skill_support import HUB, make_ctx

STOP = AlarmControlIntent(AlarmControlKind.STOP)


def _understander(ringing: dict[str, str]) -> Understander:
    memory = ConversationMemory(60.0, 300.0, lambda: 0.0)
    target = TargetMemory(
        ("light.living_ceiling",), "living", "light", DeviceAction.TURN_OFF, CommandParams()
    )
    memory.remember_target(HUB, target)
    return Understander(
        [
            AlarmControlAttempt(lambda hub: ringing.get(hub, "")),
            ContextAttempt([FollowupResolver(memory)]),
            MatcherAttempt(),
        ]
    )


@pytest.mark.parametrize("text", ["그만", "그만둬", "됐어", "취소", "꺼", "알람 꺼"])
async def test_ringing_beats_followup_dismiss(text: str) -> None:
    result = await _understander({HUB: "a1"}).understand(text, make_ctx())

    assert result.intent == STOP


@pytest.mark.parametrize("text", ["그만", "그만둬", "됐어", "취소"])
async def test_without_ringing_followup_dismiss_is_unchanged(text: str) -> None:
    result = await _understander({}).understand(text, make_ctx())

    assert result.intent == Dismiss()
    assert result.step is Step.CONTEXT


async def test_ringing_snooze_beats_followup() -> None:
    result = await _understander({HUB: "a1"}).understand("5분 뒤에 다시", make_ctx())

    assert result.intent == AlarmControlIntent(AlarmControlKind.SNOOZE, 5)


async def test_other_hub_is_untouched_by_ringing() -> None:
    result = await _understander({HUB: "a1"}).understand("그만", make_ctx("hub-2"))

    assert result.intent != STOP


async def test_followup_phrase_is_still_followup_while_ringing() -> None:
    result = await _understander({HUB: "a1"}).understand("다시 켜줘", make_ctx())

    assert isinstance(result.intent, DeviceCommand)
    assert result.step is Step.CONTEXT
