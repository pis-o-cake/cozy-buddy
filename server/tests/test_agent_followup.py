"""후속 지시 해석기와 대화 기억 테스트."""

import pytest

from app.domain.agent.memory import ConversationMemory, TargetMemory, target_from_intent
from app.domain.agent.model import (
    CommandParams,
    DeviceAction,
    DeviceCommand,
    Dismiss,
    Intent,
    SceneCommand,
    StateQuery,
)
from app.domain.agent.understand.followup import FollowupResolver
from tests.agent_skill_support import HUB, make_ctx

LIVING_LIGHTS = ("light.living_ceiling", "light.living_stand")
BEDROOM_LIGHTS = ("light.bedroom_ceiling", "light.bedroom_stand")
OFF = DeviceAction.TURN_OFF
ON = DeviceAction.TURN_ON
BRIGHT = DeviceAction.SET_BRIGHTNESS
NO_PARAMS = CommandParams()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def lights(action: DeviceAction | None = OFF, params: CommandParams = NO_PARAMS) -> TargetMemory:
    return TargetMemory(LIVING_LIGHTS, "living", "light", action, params)


def single(entity_id: str, domain: str, action: DeviceAction | None = ON) -> TargetMemory:
    return TargetMemory((entity_id,), "", domain, action, NO_PARAMS)


def resolver_with(target: TargetMemory | None, clock: Clock | None = None) -> FollowupResolver:
    memory = ConversationMemory(60.0, 300.0, clock or Clock())
    if target is not None:
        memory.remember_target(HUB, target)
    return FollowupResolver(memory)


def cmd(ids: tuple[str, ...], action: DeviceAction, **params: int) -> DeviceCommand:
    return DeviceCommand(ids, action, CommandParams(**params))


MIXED = TargetMemory(("light.living_ceiling", "fan.living_fan"), "living", "", ON, NO_PARAMS)

CASES: list[tuple[str, TargetMemory, Intent | None]] = [
    ("그거 켜줘", lights(), cmd(LIVING_LIGHTS, ON)),
    ("그거 다시 켜줘", lights(), cmd(LIVING_LIGHTS, ON)),
    ("그것 다시 켜줘", lights(), cmd(LIVING_LIGHTS, ON)),
    ("걔 켜줘", lights(), cmd(LIVING_LIGHTS, ON)),
    ("다시 켜줘", lights(), cmd(LIVING_LIGHTS, ON)),
    ("다시 꺼줘", lights(ON), cmd(LIVING_LIGHTS, OFF)),
    ("그거 꺼줘", lights(ON), cmd(LIVING_LIGHTS, OFF)),
    ("다시 해줘", lights(OFF), cmd(LIVING_LIGHTS, OFF)),
    ("더 밝게", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_delta_pct=30)),
    ("더 밝게 해줘", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_delta_pct=30)),
    ("조금 더 밝게", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_delta_pct=15)),
    ("조금 더 어둡게", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_delta_pct=-15)),
    ("좀 어둡게 해줘", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_delta_pct=-30)),
    ("최대로", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_pct=100)),
    ("가장 밝게", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_pct=100)),
    ("최소로", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_pct=10)),
    ("50퍼센트로 해줘", lights(), cmd(LIVING_LIGHTS, BRIGHT, brightness_pct=50)),
    ("아니 침실 거", lights(), cmd(BEDROOM_LIGHTS, OFF)),
    ("아니 침실 거로", lights(), cmd(BEDROOM_LIGHTS, OFF)),
    ("침실 것도", lights(ON), cmd(BEDROOM_LIGHTS, ON)),
    ("침실 걸로 해줘", lights(), cmd(BEDROOM_LIGHTS, OFF)),
    ("아니 안방 거", lights(), cmd(BEDROOM_LIGHTS, OFF)),
    ("아니 침실 거 켜줘", lights(OFF), cmd(BEDROOM_LIGHTS, ON)),
    (
        "아니 침실 거",
        lights(BRIGHT, CommandParams(brightness_pct=40)),
        cmd(BEDROOM_LIGHTS, BRIGHT, brightness_pct=40),
    ),
    ("아니 침실 거", lights(None), StateQuery(BEDROOM_LIGHTS)),
    ("그대로 둬", lights(), Dismiss()),
    ("취소", lights(), Dismiss()),
    # 기억이 있어도 지원하지 않는 형태는 매처·LLM 몫이다.
    ("켜줘", lights(), None),
    ("거실 조명 켜줘", lights(), None),
    ("침실 조명 켜줘", lights(), None),
    ("침실", lights(), None),
    ("거실 불 더 밝게", lights(), None),
    ("오늘 날씨 어때", lights(), None),
    ("아니 주방 거", lights(), cmd(("light.kitchen_ceiling",), OFF)),
    ("아니 아이방 거", lights(), None),
    # 도메인을 넘어서 추측하지 않는다.
    ("그거 켜줘", single("sensor.living_temp", "sensor", None), None),
    ("더 밝게", single("fan.living_fan", "fan"), None),
    ("그거 켜줘", single("cover.bedroom_curtain", "cover"), None),
    ("아니 침실 거", single("fan.living_fan", "fan"), None),
    ("그거 켜줘", MIXED, cmd(("light.living_ceiling", "fan.living_fan"), ON)),
    ("아니 침실 거", MIXED, None),
    ("다시 해줘", single("sensor.living_temp", "sensor", None), None),
    ("다시 해줘", single("scene.goodnight", "scene", None), SceneCommand("scene.goodnight")),
    ("그거 켜줘", single("light.gone", "light"), None),
]


@pytest.mark.parametrize(("text", "target", "expected"), CASES)
async def test_followup_resolution_table(
    text: str, target: TargetMemory, expected: Intent | None
) -> None:
    assert await resolver_with(target).resolve(text, make_ctx()) == expected


async def test_no_memory_returns_none_for_every_supported_form() -> None:
    resolver = resolver_with(None)
    ctx = make_ctx()
    for text in ("그거 켜줘", "다시 켜줘", "더 밝게", "아니 침실 거", "그대로 둬"):
        assert await resolver.resolve(text, ctx) is None


async def test_expired_memory_returns_none() -> None:
    clock = Clock()
    resolver = resolver_with(lights(), clock)
    ctx = make_ctx()
    assert await resolver.resolve("그거 켜줘", ctx) is not None

    clock.now = 61.0

    assert await resolver.resolve("그거 켜줘", ctx) is None
    assert await resolver.resolve("더 밝게", ctx) is None


async def test_memory_is_per_hub() -> None:
    resolver = resolver_with(lights())

    assert await resolver.resolve("그거 켜줘", make_ctx(hub_id="other-hub")) is None


def test_memory_keeps_last_three_turns_and_expires_them() -> None:
    clock = Clock()
    memory = ConversationMemory(60.0, 300.0, clock)
    for i in range(5):
        memory.add_turn(HUB, f"u{i}", f"a{i}")
    assert [t.user for t in memory.recent_turns(HUB)] == ["u2", "u3", "u4"]

    clock.now = 301.0

    assert memory.recent_turns(HUB) == []


def test_memory_ignores_unknown_hub_and_empty_replies() -> None:
    memory = ConversationMemory(60.0, 300.0, Clock())
    memory.add_turn("", "u", "a")
    memory.add_turn(HUB, "u", "")
    memory.remember_target("", lights())

    assert memory.recent_turns("") == [] and memory.recent_turns(HUB) == []
    assert memory.target("") is None


def test_target_from_intent_summarizes_domain_and_area() -> None:
    ctx = make_ctx()

    light = target_from_intent(cmd(LIVING_LIGHTS, OFF), ctx.view)
    mixed = target_from_intent(cmd(("light.living_ceiling", "fan.living_fan"), ON), ctx.view)
    scene = target_from_intent(SceneCommand("scene.goodnight"), ctx.view)

    assert light == lights()
    assert mixed is not None and (mixed.domain, mixed.area_id) == ("", "living")
    assert scene is not None and (scene.domain, scene.area_id, scene.action) == ("scene", "", None)
    assert target_from_intent(cmd(("light.unknown",), ON), ctx.view) is None
