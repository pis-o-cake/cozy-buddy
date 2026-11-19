"""리마인더 스킬·LLM 툴·문구·저장 매핑 테스트."""

from datetime import timedelta

import pytest
from cozy.v1 import alarm_pb2

from app.domain.agent.model import CancelAllAlarms, ReminderIntent
from app.domain.agent.reminder_text import fired_text, honorific_what
from app.domain.agent.skills.alarm_tools import alarm_tool_specs
from app.domain.alarm.models import Alarm
from app.domain.alarm.service import apply_proto, to_proto
from tests.agent_skill_support import HUB, NOW, alarm_row, make_ctx, translate
from tests.test_agent_skills import alarm_skill


async def test_reminder_intent_creates_reminder_alarm_with_label() -> None:
    skill, store, _ = alarm_skill()
    intent = ReminderIntent(
        "병원 가기", 15, 0, one_shot_epoch_ms=int((NOW + timedelta(days=1)).timestamp() * 1000)
    )

    result = await skill.execute(intent, make_ctx())

    alarm = store.created.pop()
    assert alarm.kind == alarm_pb2.ALARM_KIND_REMINDER
    assert alarm.label == "병원 가기" and alarm.hub_id == HUB
    assert alarm.one_shot.fires_at_epoch_ms == intent.one_shot_epoch_ms
    assert result.ok
    assert result.text == "내일 오후 3시에 병원 가기 리마인더를 맞췄어요"


async def test_repeating_reminder_keeps_days_and_confirms_in_korean() -> None:
    skill, store, _ = alarm_skill()

    result = await skill.execute(
        ReminderIntent("약 먹기", 8, 0, days=(1, 2, 3, 4, 5, 6, 7)), make_ctx()
    )

    alarm = store.created.pop()
    assert alarm.kind == alarm_pb2.ALARM_KIND_REMINDER
    assert (alarm.repeating.hour, alarm.repeating.minute) == (8, 0)
    assert list(alarm.repeating.days) == [1, 2, 3, 4, 5, 6, 7]
    assert result.text == "매일 오전 8시에 울리는 약 먹기 리마인더를 맞췄어요"


async def test_reminder_without_hub_fails() -> None:
    skill, store, _ = alarm_skill()

    result = await skill.execute(ReminderIntent("약 먹기", 8, 0, days=(1,)), make_ctx(hub_id=""))

    assert not result.ok and not store.created


async def test_set_reminder_tool_with_delay() -> None:
    skill, store, _ = alarm_skill()

    result = await skill.run_tool(
        "set_reminder", {"message": "빨래 꺼내기", "delay_seconds": 1800}, make_ctx()
    )

    alarm = store.created.pop()
    assert alarm.kind == alarm_pb2.ALARM_KIND_REMINDER and alarm.label == "빨래 꺼내기"
    assert alarm.one_shot.fires_at_epoch_ms == int((NOW + timedelta(minutes=30)).timestamp() * 1000)
    assert result.ok and result.payload["message"] == "빨래 꺼내기"


async def test_set_reminder_tool_with_clock_and_days() -> None:
    skill, store, _ = alarm_skill()

    result = await skill.run_tool(
        "set_reminder", {"message": "약 먹기", "hour": 8, "minute": 0, "days": [2, 1]}, make_ctx()
    )

    alarm = store.created.pop()
    assert list(alarm.repeating.days) == [1, 2]
    assert result.payload["time"] == "08:00"


async def test_set_reminder_tool_with_date_uses_that_day() -> None:
    skill, store, _ = alarm_skill()

    await skill.run_tool(
        "set_reminder",
        {"message": "병원 가기", "hour": 15, "minute": 0, "date": "2026-11-14"},
        make_ctx(),
    )

    # 2026-11-14 15:00 KST
    assert store.created.pop().one_shot.fires_at_epoch_ms == 1_794_636_000_000


@pytest.mark.parametrize(
    "args",
    [
        {"message": "  "},
        {"message": "병원 가기"},
        {"message": "병원 가기", "delay_seconds": 0},
        {"message": "병원 가기", "hour": 24, "minute": 0},
    ],
)
async def test_set_reminder_tool_rejects_bad_arguments(args: dict[str, object]) -> None:
    skill, store, _ = alarm_skill()

    with pytest.raises((ValueError, KeyError)):
        await skill.run_tool("set_reminder", args, make_ctx())
    assert not store.created


def test_set_reminder_tool_is_exposed_to_the_llm() -> None:
    spec = next(s for s in alarm_tool_specs() if s["function"]["name"] == "set_reminder")

    assert spec["function"]["parameters"]["required"] == ["message"]


async def test_alarm_cancel_leaves_reminders_alone() -> None:
    skill, store, _ = alarm_skill()
    store.rows = [alarm_row("r1", kind="reminder", hour=None, minute=None, label="약 먹기")]

    await skill.execute(CancelAllAlarms(), make_ctx())

    assert store.deleted == []


@pytest.mark.parametrize(
    ("label", "spoken"),
    [
        ("병원 가기", "병원 가실 시간이에요"),
        ("약 먹기", "약 먹으실 시간이에요"),
        ("빨래 꺼내기", "빨래 꺼내실 시간이에요"),
        ("운동하기", "운동하실 시간이에요"),
        ("문 잠그기", "문 잠그실 시간이에요"),
        ("밥 만들기", "밥 만드실 시간이에요"),
        ("화분에 물 주기", "화분에 물 주실 시간이에요"),
        ("회의", "회의 시간이에요"),
        ("기", "기 시간이에요"),
        ("  병원 가기 ", "병원 가실 시간이에요"),
    ],
)
def test_fired_text_uses_honorific_and_handles_final_consonants(label: str, spoken: str) -> None:
    assert fired_text(label, translate) == spoken


def test_honorific_leaves_non_hangul_stem_alone() -> None:
    assert honorific_what("A1기") == "A1기"


def test_alarm_row_roundtrips_reminder_kind() -> None:
    source = alarm_pb2.Alarm(
        alarm_id="m1",
        kind=alarm_pb2.ALARM_KIND_REMINDER,
        label="병원 가기",
        enabled=True,
        one_shot=alarm_pb2.OneShot(fires_at_epoch_ms=123),
    )
    row = Alarm(hub_id=HUB, alarm_id="m1", revision=2)

    apply_proto(row, source)
    restored = to_proto(row)

    assert row.kind == "reminder"
    assert restored.kind == alarm_pb2.ALARM_KIND_REMINDER
    assert restored.label == "병원 가기"
    assert restored.one_shot.fires_at_epoch_ms == 123


def test_timer_and_alarm_kinds_still_map() -> None:
    for kind, name in (
        (alarm_pb2.ALARM_KIND_TIMER, "timer"),
        (alarm_pb2.ALARM_KIND_ALARM, "alarm"),
    ):
        row = Alarm(hub_id=HUB, alarm_id="x", revision=0)
        apply_proto(row, alarm_pb2.Alarm(alarm_id="x", kind=kind, one_shot=alarm_pb2.OneShot()))
        assert row.kind == name
        assert to_proto(row).kind == kind
