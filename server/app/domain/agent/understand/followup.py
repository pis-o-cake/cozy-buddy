"""후속 지시 해석: 직전 턴의 대상에 기대는 짧은 발화("그거 다시 켜줘", "더 밝게")."""

import re

from app.domain.agent.context import TurnContext
from app.domain.agent.memory import ConversationMemory, TargetMemory
from app.domain.agent.model import (
    RELATIVE_BRIGHTNESS_STEP,
    CommandParams,
    DeviceAction,
    DeviceCommand,
    Dismiss,
    Intent,
    SceneCommand,
    StateQuery,
)
from app.domain.agent.normalize import compact_form, normalize

SMALL_BRIGHTNESS_STEP = 15
MIN_BRIGHTNESS_PCT = 10
MAX_BRIGHTNESS_PCT = 100
# 후속 지시로 켜고 끌 수 있는 도메인. 그 밖의 도메인은 추측하지 않는다.
_SWITCHABLE = frozenset({"light", "switch", "fan", "climate"})

_PRONOUN = r"(?:그거|그것|그걸|걔|저거|이거|이것)"
_AGAIN = r"(?:다시|또)"
_ON = r"(?:켜|켜라|틀어|틀어라)"
_OFF = r"(?:꺼|꺼라|끄|끄라)"
_TOGGLE_FULL = re.compile(
    rf"^(?:{_PRONOUN}{_AGAIN}?|{_AGAIN}{_PRONOUN}?)(?P<on>{_ON})?(?P<off>{_OFF})?$"
)
_REPEAT = re.compile(rf"^(?:{_PRONOUN}{_AGAIN}|{_AGAIN}{_PRONOUN}?)$")
_BRIGHT_UP = re.compile(rf"^{_PRONOUN}?(?:밝게|밝혀|환하게|밝아지게)$")
_BRIGHT_DOWN = re.compile(rf"^{_PRONOUN}?(?:어둡게|어둑하게|흐리게|어두워지게)$")
_BRIGHT_MAX = re.compile(
    rf"^{_PRONOUN}?(?:최대로?|최대한밝게|가장밝게|제일밝게|최대로밝게|최대밝기로?|풀로)$"
)
_BRIGHT_MIN = re.compile(
    rf"^{_PRONOUN}?(?:최소로?|최대한어둡게|가장어둡게|제일어둡게|최소로어둡게|최소밝기로?)$"
)
_BRIGHT_PCT = re.compile(rf"^{_PRONOUN}?(?P<pct>\d{{1,3}})%(?:로|까지)?(?:맞춰|바꿔|해)?$")
_RETARGET_NO = re.compile(r"^(?:아니야|아니요|아니|아냐)")
_RETARGET_NOUN = re.compile(r"^(?:거로|걸로|거도|것도|것으로|거|걸|것|으로|로|도|쪽으로|쪽도|쪽)")
_RETARGET_ACTION = re.compile(rf"^(?P<on>{_ON})?(?P<off>{_OFF})?$")
_DISMISS = frozenset(
    {"그대로둬", "그대로놔둬", "둬", "놔둬", "그대로", "취소", "됐어", "그만", "그만둬"}
)
_SMALL_WORDS = ("조금", "약간", "살짝")


class FollowupResolver:
    """직전 턴의 대상에 대한 후속 지시를 규칙으로만 해석한다.

    기억이 없거나 만료됐으면, 발화가 지원하는 형태가 아니면 None 을 돌려 매처·LLM 으로 넘긴다.
    도메인이 다른 대상으로는 절대 넘어가지 않는다.
    """

    def __init__(self, memory: ConversationMemory) -> None:
        self._memory = memory

    async def resolve(self, text: str, ctx: TurnContext) -> Intent | None:
        target = self._memory.target(ctx.hub_id)
        if target is None or not _all_known(target, ctx):
            return None
        compact = normalize(text).compact
        if not compact:
            return None
        if compact in _DISMISS:
            return Dismiss()
        return (
            _toggle(compact, target, ctx)
            or _repeat(compact, target, ctx)
            or _brightness(compact, text, target)
            or _retarget(compact, target, ctx)
        )


def _all_known(target: TargetMemory, ctx: TurnContext) -> bool:
    return all(ctx.view.entity(i) is not None for i in target.entity_ids)


def _switchable(target: TargetMemory, ctx: TurnContext) -> bool:
    """대상이 모두 켜고 끌 수 있는 도메인인가. 도메인이 섞여 있어도 각각이 그렇다면 허용한다."""
    entities = [ctx.view.entity(i) for i in target.entity_ids]
    return all(e is not None and e.domain in _SWITCHABLE for e in entities)


def _toggle(compact: str, target: TargetMemory, ctx: TurnContext) -> Intent | None:
    """ "그거 켜줘", "다시 꺼줘". 지시어나 "다시" 없이 동작만 말한 발화는 매처 몫이다."""
    match = _TOGGLE_FULL.match(compact)
    if match is None or not _switchable(target, ctx):
        return None
    if bool(match["on"]) == bool(match["off"]):
        return None
    action = DeviceAction.TURN_ON if match["on"] else DeviceAction.TURN_OFF
    return DeviceCommand(target.entity_ids, action)


def _repeat(compact: str, target: TargetMemory, ctx: TurnContext) -> Intent | None:
    """ "다시 해줘", "그거 또". 직전과 같은 동작을 같은 대상에 되풀이한다."""
    if _REPEAT.match(compact) is None:
        return None
    if target.domain == "scene":
        return SceneCommand(target.entity_ids[0]) if len(target.entity_ids) == 1 else None
    if target.action is None or not _switchable(target, ctx):
        return None
    return DeviceCommand(target.entity_ids, target.action, target.params)


def _brightness(compact: str, raw: str, target: TargetMemory) -> Intent | None:
    if target.domain != "light":
        return None
    params = _brightness_params(compact, raw)
    if params is None:
        return None
    return DeviceCommand(target.entity_ids, DeviceAction.SET_BRIGHTNESS, params)


def _brightness_params(compact: str, raw: str) -> CommandParams | None:
    step = SMALL_BRIGHTNESS_STEP if _is_small(raw) else RELATIVE_BRIGHTNESS_STEP
    if _BRIGHT_UP.match(compact):
        return CommandParams(brightness_delta_pct=step)
    if _BRIGHT_DOWN.match(compact):
        return CommandParams(brightness_delta_pct=-step)
    if _BRIGHT_MAX.match(compact):
        return CommandParams(brightness_pct=MAX_BRIGHTNESS_PCT)
    if _BRIGHT_MIN.match(compact):
        return CommandParams(brightness_pct=MIN_BRIGHTNESS_PCT)
    pct = _BRIGHT_PCT.match(compact)
    if pct is None:
        return None
    value = int(pct["pct"])
    if value < 1 or value > MAX_BRIGHTNESS_PCT:
        return None
    return CommandParams(brightness_pct=max(MIN_BRIGHTNESS_PCT, value))


def _is_small(raw: str) -> bool:
    compact = compact_form(raw)
    return any(word in compact for word in _SMALL_WORDS)


def _retarget(compact: str, target: TargetMemory, ctx: TurnContext) -> Intent | None:
    """ "아니 침실 거", "침실 것도". 같은 동작을 다른 방의 같은 도메인 엔티티에 보낸다."""
    if not target.domain:
        return None
    area_id, rest = _split_area(compact, ctx)
    if area_id is None:
        return None
    no_prefix = _RETARGET_NO.match(rest)
    rest = rest[no_prefix.end() :] if no_prefix else rest
    noun = _RETARGET_NOUN.match(rest)
    rest = rest[noun.end() :] if noun else rest
    if not (no_prefix or noun):
        return None
    action = _RETARGET_ACTION.match(rest)
    if action is None or (action["on"] and action["off"]):
        return None
    explicit = (
        DeviceAction.TURN_ON if action["on"] else DeviceAction.TURN_OFF if action["off"] else None
    )
    entity_ids = tuple(
        e.entity_id
        for e in ctx.view.model.entities
        if e.area_id == area_id and e.domain == target.domain
    )
    if not entity_ids:
        return None
    return _retargeted(entity_ids, target, explicit, ctx)


def _retargeted(
    entity_ids: tuple[str, ...],
    target: TargetMemory,
    explicit: DeviceAction | None,
    ctx: TurnContext,
) -> Intent | None:
    action = explicit or target.action
    if action is None:
        # 동작 없이 상태만 물었던 대상은 같은 질문을 다른 방에 던진다.
        return StateQuery(entity_ids)
    if not _switchable(target, ctx):
        return None
    params = target.params if action is target.action else CommandParams()
    return DeviceCommand(entity_ids, action, params)


def _split_area(compact: str, ctx: TurnContext) -> tuple[str | None, str]:
    """발화에서 방 이름을 하나만 찾아 떼어낸다. 없거나 둘 이상이면 (None, 원문)."""
    hits: list[tuple[str, str]] = []
    for area in ctx.view.model.areas:
        for name in (area.name, *area.aliases):
            key = compact_form(name)
            if key and key in compact:
                hits.append((area.area_id, key))
    if len({area_id for area_id, _ in hits}) != 1:
        return None, compact
    area_id, key = max(hits, key=lambda h: len(h[1]))
    return area_id, compact.replace(key, "", 1)
