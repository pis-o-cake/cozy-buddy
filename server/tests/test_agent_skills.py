"""스킬 단위 테스트: 결정론 의도와 LLM 툴 호출이 같은 HA·알람 호출을 만든다."""

from datetime import timedelta

import pytest
from cozy.v1 import alarm_pb2

from app.domain.agent.clarify import ClarifyStore
from app.domain.agent.model import (
    AlarmIntent,
    CancelAlarmIntent,
    CancelAllAlarms,
    CancelSelected,
    Clarify,
    ClarifyReason,
    CommandParams,
    DeviceAction,
    DeviceCommand,
    Dismiss,
    SceneCommand,
    StateQuery,
    TimerIntent,
    WeatherIntent,
)
from app.domain.agent.skills.alarm import AlarmSkill
from app.domain.agent.skills.alarm_select import AlarmSelector
from app.domain.agent.skills.clarify import ClarifySkill
from app.domain.agent.skills.device import DeviceSkill
from app.domain.agent.skills.registry import SkillRegistry
from app.domain.agent.skills.state import StateSkill
from app.domain.agent.skills.weather import WeatherSkill
from app.domain.weather.schemas import WeatherWhen
from tests.agent_skill_support import (
    HUB,
    NOW,
    TZ,
    FakeAlarmStore,
    FakeHome,
    alarm_row,
    make_ctx,
    translate,
)

LIGHT = "light.living_ceiling"


def device_skill() -> tuple[DeviceSkill, FakeHome]:
    home = FakeHome()
    return DeviceSkill(translate, home), home  # type: ignore[arg-type]


def alarm_skill() -> tuple[AlarmSkill, FakeAlarmStore, ClarifyStore]:
    store = FakeAlarmStore()
    clarify = ClarifyStore(30.0)
    skill = AlarmSkill(translate, store, AlarmSelector(store, TZ), clarify, TZ)
    return skill, store, clarify


# --- DeviceSkill -----------------------------------------------------------------------


async def test_device_intent_and_tool_send_same_command() -> None:
    skill, home = device_skill()
    intent = DeviceCommand((LIGHT,), DeviceAction.TURN_OFF)

    via_intent = await skill.execute(intent, make_ctx())
    via_tool = await skill.run_tool(
        "control_device", {"entity_id": LIGHT, "action": "turn_off"}, make_ctx()
    )

    assert home.calls() == [(LIGHT, "turn_off", {})] * 2
    assert via_intent.ok and via_tool.ok
    assert via_intent.entities == via_tool.entities == (LIGHT,)
    assert via_intent.text
    assert via_tool.payload == {"ok": True}


async def test_relative_brightness_is_clamped_from_current_value_on_both_paths() -> None:
    skill, home = device_skill()
    ctx = make_ctx()  # 128/255 -> 50%
    params = CommandParams(brightness_delta_pct=30)

    await skill.execute(DeviceCommand((LIGHT,), DeviceAction.SET_BRIGHTNESS, params), ctx)
    await skill.run_tool(
        "control_device",
        {"entity_id": LIGHT, "action": "set_brightness", "brightness_delta_pct": 30},
        ctx,
    )

    assert home.calls() == [(LIGHT, "set_brightness", {"brightness_pct": "80"})] * 2


async def test_scene_and_script_actions() -> None:
    skill, home = device_skill()

    result = await skill.execute(SceneCommand("scene.goodnight"), make_ctx())
    await skill.run_tool(
        "control_device", {"entity_id": "scene.goodnight", "action": "activate"}, make_ctx()
    )

    assert home.calls() == [("scene.goodnight", "activate", {})] * 2
    assert result.ok


async def test_device_failure_reports_ha_message_key_on_both_paths() -> None:
    skill, home = device_skill()
    home.fail_key = "home.unavailable"

    via_intent = await skill.execute(DeviceCommand((LIGHT,), DeviceAction.TURN_ON), make_ctx())
    via_tool = await skill.run_tool(
        "control_device", {"entity_id": LIGHT, "action": "turn_on"}, make_ctx()
    )

    assert not via_intent.ok and not via_tool.ok
    assert via_intent.response_key == via_tool.response_key == "home.unavailable"
    assert via_tool.payload == {"ok": False, "error": translate("home.unavailable")}


@pytest.mark.parametrize(
    "args",
    [
        {"entity_id": "light.nope", "action": "turn_on"},
        {"entity_id": LIGHT, "action": "explode"},
        {"entity_id": LIGHT, "action": "set_brightness"},
        {"entity_id": LIGHT, "action": "set_brightness", "brightness_pct": 101},
    ],
)
async def test_invalid_control_arguments_raise_and_send_nothing(args: dict[str, object]) -> None:
    skill, home = device_skill()

    with pytest.raises(ValueError):
        await skill.run_tool("control_device", args, make_ctx())

    assert home.commands == []


def test_control_tool_lists_only_controllable_entities() -> None:
    skill, _ = device_skill()

    (tool,) = skill.tool_specs(make_ctx().view)

    enum = tool["function"]["parameters"]["properties"]["entity_id"]["enum"]
    assert LIGHT in enum and "sensor.living_temp" not in enum


# --- StateSkill ------------------------------------------------------------------------


async def test_state_skill_answers_query_and_tool_from_same_cache() -> None:
    skill = StateSkill(translate)
    ctx = make_ctx()

    answer = await skill.execute(StateQuery((LIGHT,)), ctx)
    tool = await skill.run_tool("get_device_state", {"entity_id": LIGHT}, ctx)

    assert answer.ok and "켜져" in answer.text
    assert tool.payload["state"] == "on" and tool.payload["brightness_pct"] == 50


async def test_state_query_without_known_entity_fails() -> None:
    skill = StateSkill(translate)

    result = await skill.execute(StateQuery(("light.nope",)), make_ctx())

    assert not result.ok
    with pytest.raises(ValueError):
        await skill.run_tool("get_device_state", {"entity_id": "light.nope"}, make_ctx())


# --- AlarmSkill ------------------------------------------------------------------------


async def test_timer_intent_and_tool_create_same_alarm_and_remember_length() -> None:
    skill, store, _ = alarm_skill()
    ctx = make_ctx()

    via_intent = await skill.execute(TimerIntent(300, "라면"), ctx)
    via_tool = await skill.run_tool("set_timer", {"duration_seconds": 300, "label": "라면"}, ctx)

    assert via_intent.ok and via_tool.payload == {"ok": True, "duration_seconds": 300}
    first, second = store.created
    for alarm in (first, second):
        assert alarm.kind == alarm_pb2.ALARM_KIND_TIMER
        assert alarm.label == "라면" and alarm.hub_id == HUB
        assert alarm.one_shot.fires_at_epoch_ms == int(
            (NOW + timedelta(seconds=300)).timestamp() * 1000
        )
        assert store.original_timer_seconds(HUB, alarm.alarm_id) == 300


async def test_repeating_alarm_intent_and_tool_create_same_alarm() -> None:
    skill, store, _ = alarm_skill()
    ctx = make_ctx()

    await skill.execute(AlarmIntent(7, 30, days=(1, 2, 3)), ctx)
    result = await skill.run_tool("set_alarm", {"hour": 7, "minute": 30, "days": [3, 1, 2]}, ctx)

    assert result.payload == {"ok": True, "days": [1, 2, 3], "time": "07:30"}
    for alarm in store.created:
        assert (alarm.repeating.hour, alarm.repeating.minute) == (7, 30)
        assert list(alarm.repeating.days) == [1, 2, 3]


async def test_one_shot_tool_rolls_to_next_day_and_intent_matches() -> None:
    skill, store, _ = alarm_skill()
    ctx = make_ctx()  # 15:30
    tomorrow_7 = (NOW + timedelta(days=1)).replace(hour=7, minute=0)

    result = await skill.run_tool("set_alarm", {"hour": 7, "minute": 0}, ctx)
    await skill.execute(
        AlarmIntent(7, 0, one_shot_epoch_ms=int(tomorrow_7.timestamp() * 1000)), ctx
    )

    assert result.payload["fires_at"] == tomorrow_7.isoformat(timespec="minutes")
    assert [a.one_shot.fires_at_epoch_ms for a in store.created] == [
        int(tomorrow_7.timestamp() * 1000)
    ] * 2


async def test_set_alarm_tool_rejects_past_date() -> None:
    skill, store, _ = alarm_skill()

    with pytest.raises(ValueError):
        await skill.run_tool(
            "set_alarm", {"hour": 7, "minute": 0, "date": "2026-11-12"}, make_ctx()
        )

    assert store.created == []


async def test_alarm_without_hub_fails_on_both_paths() -> None:
    skill, store, _ = alarm_skill()
    ctx = make_ctx(hub_id="")

    via_intent = await skill.execute(TimerIntent(60), ctx)
    via_tool = await skill.run_tool("set_timer", {"duration_seconds": 60}, ctx)

    assert not via_intent.ok and not via_tool.ok
    assert via_intent.response_key == via_tool.response_key == "agent.hub_required"
    assert store.created == []


async def test_cancel_intent_and_tool_delete_same_row() -> None:
    skill, store, _ = alarm_skill()
    store.rows = [alarm_row("a1", hour=7, minute=0), alarm_row("a2", hour=8, minute=0)]
    ctx = make_ctx()

    via_intent = await skill.execute(CancelAlarmIntent(hour=7, minute=0), ctx)
    via_tool = await skill.run_tool("cancel_alarm", {"hour": 8, "minute": 0}, ctx)

    assert store.deleted == ["a1", "a2"]
    assert via_intent.ok and via_tool.payload == {"ok": True}


async def test_ambiguous_cancel_asks_and_remembers_candidates() -> None:
    skill, store, clarify = alarm_skill()
    store.rows = [alarm_row("a1", hour=7), alarm_row("a2", hour=8)]

    result = await skill.execute(CancelAlarmIntent(), make_ctx())
    tool = await skill.run_tool("cancel_alarm", {}, make_ctx())

    assert store.deleted == []
    assert "첫 번째" in result.text
    assert tool.payload == {"ok": False, "error": "ambiguous"}
    pending = clarify.take(HUB)
    assert pending is not None and pending.reason is ClarifyReason.AMBIGUOUS_ALARM
    assert pending.candidate_alarm_ids == ("a1", "a2")


async def test_cancel_without_match_reports_not_found() -> None:
    skill, _, _ = alarm_skill()

    result = await skill.execute(CancelAlarmIntent(label="없는"), make_ctx())
    tool = await skill.run_tool("cancel_alarm", {"label": "없는"}, make_ctx())

    assert result.response_key == "agent.alarm.not_found" and not result.ok
    assert tool.payload == {"ok": False, "error": "none"}


async def test_cancel_selected_needs_confirmation_when_many() -> None:
    skill, store, clarify = alarm_skill()
    store.rows = [alarm_row("a1"), alarm_row("a2")]

    asked = await skill.execute(CancelSelected(("a1", "a2")), make_ctx())
    assert store.deleted == [] and clarify.take(HUB) is not None

    done = await skill.execute(CancelSelected(("a1", "a2"), confirmed=True), make_ctx())
    assert store.deleted == ["a1", "a2"]
    assert asked.ok and done.response_key == "agent.alarm.cancel_all"


async def test_cancel_selected_single_timer_and_missing() -> None:
    skill, store, _ = alarm_skill()
    store.rows = [alarm_row("t1", kind="timer", hour=None, minute=None, fires_at_epoch_ms=1)]

    gone = await skill.execute(CancelSelected(("zzz",)), make_ctx())
    done = await skill.execute(CancelSelected(("t1",)), make_ctx())

    assert gone.response_key == "agent.alarm.not_found"
    assert done.response_key == "agent.timer.cancel" and store.deleted == ["t1"]


async def test_cancel_all_alarms() -> None:
    skill, store, _ = alarm_skill()
    store.rows = [alarm_row("a1"), alarm_row("t1", kind="timer", hour=None, minute=None)]

    result = await skill.execute(CancelAllAlarms(), make_ctx())
    empty = await skill.execute(CancelAllAlarms(), make_ctx())

    assert store.deleted == ["a1", "t1"]
    assert result.response_key == "agent.alarm.cancel_all" and not empty.ok


# --- WeatherSkill / ClarifySkill / Registry --------------------------------------------


async def test_weather_disabled_on_both_paths() -> None:
    skill = WeatherSkill(translate, None)

    result = await skill.execute(WeatherIntent(WeatherWhen.NOW), make_ctx())
    tool = await skill.run_tool("get_weather", {"when": "now"}, make_ctx())

    assert not result.ok and result.text == translate("weather.disabled")
    assert tool.payload == {"ok": False, "error": "disabled"}


async def test_clarify_skill_remembers_question_and_dismiss_confirms() -> None:
    store = ClarifyStore(30.0)
    skill = ClarifySkill(translate, store)
    question = Clarify(
        ClarifyReason.AMBIGUOUS_TARGET, ("light.living_ceiling", "light.living_stand")
    )

    asked = await skill.execute(question, make_ctx())
    dismissed = await skill.execute(Dismiss(), make_ctx())

    assert asked.ok and "어떤" in asked.text
    assert store.take(HUB) == question
    assert dismissed.text == translate("agent.clarify.cancelled")


async def test_registry_routes_intents_tools_and_rejects_unknown() -> None:
    home = FakeHome()
    store = FakeAlarmStore()
    clarify = ClarifyStore(30.0)
    registry = SkillRegistry(
        [
            DeviceSkill(translate, home),  # type: ignore[arg-type]
            StateSkill(translate),
            AlarmSkill(translate, store, AlarmSelector(store, TZ), clarify, TZ),
            WeatherSkill(translate, None),
            ClarifySkill(translate, clarify),
        ],
        translate,
    )
    ctx = make_ctx()

    names = [t["function"]["name"] for t in registry.tools(ctx.view)]
    assert names == [
        "control_device",
        "get_device_state",
        "set_alarm",
        "set_timer",
        "set_reminder",
        "cancel_alarm",
        "get_weather",
    ]
    assert (await registry.execute(TimerIntent(60), ctx)).ok
    assert (await registry.run_tool("nope", {}, ctx)).payload == {
        "ok": False,
        "error": "unknown_tool",
    }
    bad = await registry.run_tool("set_timer", {"duration_seconds": 0}, ctx)
    assert bad.payload == {"ok": False, "error": "invalid_arguments"}
