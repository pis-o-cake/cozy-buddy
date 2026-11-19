"""울리는 알람 음성 제어: 매처, 이해 시도, 스킬, HubControl."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cozy.v1.alarm_pb2 import (
    ALARM_CONTROL_ACTION_SNOOZE,
    ALARM_CONTROL_ACTION_STOP,
    AlarmControlResult,
)
from cozy.v1.common_pb2 import ERROR_CODE_NOT_FOUND
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import HubStatus

from app.config import ClientRole
from app.core.connections import ClientConnection, ConnectionManager
from app.domain.agent.alarm_control_attempt import AlarmControlAttempt
from app.domain.agent.matcher import match
from app.domain.agent.model import (
    AlarmControlIntent,
    AlarmControlKind,
    CancelAlarmIntent,
    MatchResult,
    NoMatch,
    UtteranceContext,
)
from app.domain.agent.skills.alarm_control import AlarmControlSkill
from tests.agent_fixtures import HOME, NOW
from tests.agent_skill_support import HUB, make_ctx, translate

STOP = AlarmControlIntent(AlarmControlKind.STOP)
SNOOZE = AlarmControlIntent(AlarmControlKind.SNOOZE)


def _match(text: str, *, ringing: bool) -> MatchResult:
    return match(UtteranceContext(text, None, NOW, ringing=ringing), HOME)


@pytest.mark.parametrize(
    "text",
    [
        "알람 꺼줘",
        "알람 꺼",
        "알람 그만",
        "그만",
        "알람 멈춰",
        "알람 멈춰줘",
        "됐어 꺼",
        "알람 소리 꺼줘",
    ],
)
def test_ringing_stop_phrases(text: str) -> None:
    assert _match(text, ringing=True) == STOP


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("5분 뒤에 다시 울려줘", 5),
        ("5분 뒤에 다시", 5),
        ("10분 후에 다시 알려줘", 10),
        ("5분만 더 있다 다시 울려", 5),
        ("조금 이따 다시 울려줘", None),
        ("이따가 다시", None),
        ("다시 울려줘", None),
        ("스누즈", None),
        ("스누즈 5분", 5),
        ("알람 5분 뒤에 다시", 5),
    ],
)
def test_ringing_snooze_phrases(text: str, minutes: int | None) -> None:
    assert _match(text, ringing=True) == AlarmControlIntent(AlarmControlKind.SNOOZE, minutes)


@pytest.mark.parametrize(
    "text",
    [
        "5분 뒤에 알람 울려줘",
        "거실 조명 꺼줘",
        "내일 7시에 알람 맞춰줘",
        "알람 취소해줘",
        "90분 뒤에 다시",
        "알람 꺼 놓고 거실 조명 켜줘",
        "7시 알람 꺼줘",
    ],
)
def test_ringing_does_not_hijack_other_phrases(text: str) -> None:
    assert not isinstance(_match(text, ringing=True), AlarmControlIntent)


def test_not_ringing_keeps_previous_behaviour() -> None:
    assert _match("알람 꺼줘", ringing=False) == CancelAlarmIntent()
    assert _match("5분 뒤에 다시 울려줘", ringing=False) == NoMatch()


async def test_attempt_only_acts_for_a_ringing_hub() -> None:
    ringing = {"hub-1": "a1"}
    attempt = AlarmControlAttempt(lambda hub: ringing.get(hub, ""))

    assert await attempt("알람 꺼줘", make_ctx("hub-1")) == STOP
    assert await attempt("알람 꺼줘", make_ctx("hub-2")) is None
    assert await attempt("알람 꺼줘", make_ctx("")) is None
    assert await attempt("거실 조명 꺼줘", make_ctx("hub-1")) is None


class _Hub:
    """허브 대역. 보낸 AlarmControl 을 기록하고 정해 둔 결과로 답한다."""

    def __init__(self, manager: ConnectionManager, *, answer: bool = True, ok: bool = True) -> None:
        self.received: list[Any] = []
        self._manager = manager
        self._answer = answer
        self._ok = ok

    async def attach(self, hub_id: str) -> None:
        socket: Any = AsyncMock()
        socket.send_bytes.side_effect = self._on_send
        conn = ClientConnection(socket)
        conn.identify(ClientRole.HUB, hub_id)
        await self._manager.register(conn)
        self._hub_id = hub_id

    async def _on_send(self, data: bytes) -> None:
        control = Envelope.FromString(data).alarm_control
        self.received.append(control)
        if not self._answer:
            return
        result = AlarmControlResult(
            request_id=control.request_id,
            alarm_id=control.alarm_id,
            ok=self._ok,
            code=0 if self._ok else ERROR_CODE_NOT_FOUND,
        )
        asyncio.get_running_loop().call_soon(
            self._manager.hub_control.resolve, self._hub_id, result
        )


async def _skill(
    *, ringing: str = "a1", online: bool = True, answer: bool = True, ok: bool = True
) -> tuple[AlarmControlSkill, _Hub, ConnectionManager]:
    manager = ConnectionManager()
    manager.hub_control._timeout = 0.2
    hub = _Hub(manager, answer=answer, ok=ok)
    if online:
        await hub.attach(HUB)
    if ringing:
        manager.hub_control.update_status(HUB, HubStatus(ringing_alarm_id=ringing))
    return AlarmControlSkill(translate, manager.hub_control), hub, manager


async def test_stop_sends_control_and_replies_after_result() -> None:
    skill, hub, manager = await _skill()

    result = await skill.execute(STOP, make_ctx())

    assert result.ok and result.text == "알람을 껐어요"
    assert [(c.alarm_id, c.action) for c in hub.received] == [("a1", ALARM_CONTROL_ACTION_STOP)]
    assert manager.hub_control.ringing_alarm_id(HUB) == ""


async def test_snooze_defaults_to_nine_minutes() -> None:
    skill, hub, _ = await _skill()

    result = await skill.execute(SNOOZE, make_ctx())

    assert result.ok and result.text == "9분 뒤에 다시 울릴게요"
    assert (hub.received[0].action, hub.received[0].snooze_minutes) == (
        ALARM_CONTROL_ACTION_SNOOZE,
        9,
    )


async def test_snooze_uses_the_spoken_minutes() -> None:
    skill, hub, _ = await _skill()

    result = await skill.execute(AlarmControlIntent(AlarmControlKind.SNOOZE, 5), make_ctx())

    assert result.text == "5분 뒤에 다시 울릴게요"
    assert hub.received[0].snooze_minutes == 5


async def test_not_ringing_fails_without_contacting_the_hub() -> None:
    skill, hub, _ = await _skill(ringing="")

    result = await skill.execute(STOP, make_ctx())

    assert not result.ok and result.text == "지금 울리는 알람이 없어요"
    assert hub.received == []


async def test_hub_without_connection_fails() -> None:
    skill, _, _ = await _skill(online=False)

    result = await skill.execute(STOP, make_ctx())

    assert not result.ok and result.response_key == "agent.alarm_control.failed"


async def test_timeout_fails() -> None:
    skill, hub, manager = await _skill(answer=False)

    result = await skill.execute(STOP, make_ctx())

    assert not result.ok and result.response_key == "agent.alarm_control.failed"
    assert len(hub.received) == 1
    assert manager.hub_control.ringing_alarm_id(HUB) == "a1"


async def test_hub_rejection_fails() -> None:
    skill, _, _ = await _skill(ok=False)

    result = await skill.execute(STOP, make_ctx())

    assert not result.ok and result.response_key == "agent.alarm_control.failed"


async def test_status_is_forgotten_when_hub_disconnects() -> None:
    manager = ConnectionManager()
    socket: Any = AsyncMock()
    conn = ClientConnection(socket)
    conn.identify(ClientRole.HUB, HUB)
    await manager.register(conn)
    manager.hub_control.update_status(HUB, HubStatus(ringing_alarm_id="a1"))

    manager.unregister(conn)

    assert manager.hub_control.ringing_alarm_id(HUB) == ""


async def test_result_from_another_hub_is_ignored() -> None:
    skill, hub, manager = await _skill(answer=False)
    task = asyncio.create_task(skill.execute(STOP, make_ctx()))
    await asyncio.sleep(0.05)

    manager.hub_control.resolve(
        "other", AlarmControlResult(request_id=hub.received[0].request_id, ok=True)
    )
    result = await task

    assert not result.ok
