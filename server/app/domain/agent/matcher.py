"""결정론 한국어 명령 매처.

흐름: 정규화 -> 취소/타이머/알람 -> 어휘 스캔(기기·장면·상태 질의) -> 방 인지 대상 해석.
확신이 없으면 항상 NoMatch 를 낸다. 틀린 추측보다 LLM 폴백이 낫다.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from loguru import logger

from app.domain.agent.lexicon import QUERY_KINDS, VERB_KINDS, Kind, Lex, Vocab, build_vocab, scan
from app.domain.agent.model import (
    RELATIVE_BRIGHTNESS_STEP,
    Clarify,
    ClarifyReason,
    CommandParams,
    DeviceAction,
    DeviceCommand,
    EntityRef,
    HomeModel,
    MatchResult,
    NoMatch,
    SceneCommand,
    StateQuery,
    UtteranceContext,
)
from app.domain.agent.normalize import NormalizedText, normalize
from app.domain.agent.schedule import match_alarm, match_cancel, match_timer
from app.domain.agent.weather_intent import match_weather

__all__ = ["match"]

_SWITCHABLE = frozenset({"light", "switch", "fan", "climate"})
_DEFAULT_ALL_DOMAINS = ("light", "switch", "fan")
_MIN_BRIGHTNESS_PCT = 10
_MAX_BRIGHTNESS_PCT = 100
_SCENE_ALLOWED = frozenset({Kind.SCENE, Kind.SCENE_WORD, Kind.SCENE_VERB, Kind.ON, Kind.SET})
_BRIGHTNESS_KINDS = frozenset({Kind.PCT, Kind.NUM, Kind.MAX, Kind.MIN})


@dataclass(frozen=True, slots=True)
class _Group:
    """발화 속 기기 지시어 하나. domain 단어(불, 선풍기) 또는 이름으로 지목된 엔티티."""

    word: str
    domain: str | None = None
    hint: str | None = None
    ids: frozenset[str] | None = None


def match(context: UtteranceContext, home: HomeModel) -> MatchResult:
    """발화를 결정론으로 해석한다.

    Args:
        context: 발화 문맥(텍스트, 허브가 놓인 방, 현재 시각).
        home: 집 구조 스냅샷.

    Returns:
        해석 결과. 확신 있게 해석하지 못하면 NoMatch.
    """
    norm = normalize(context.text)
    if not norm.compact:
        return NoMatch()
    scheduled = (
        match_cancel(norm, context.now) or match_timer(norm) or match_alarm(norm, context.now)
    )
    if scheduled is not None:
        return scheduled
    result = _match_home(norm, context, home)
    if isinstance(result, NoMatch):
        result = match_weather(norm, context.text) or result
    logger.debug("Matched utterance to {}", type(result).__name__)
    return result


def _match_home(norm: NormalizedText, context: UtteranceContext, home: HomeModel) -> MatchResult:
    vocab = build_vocab(home)
    scanned = scan(norm.compact, vocab)
    if not scanned:
        return NoMatch()
    lexes = [lex for lex in scanned if lex.kind not in (Kind.FILLER, Kind.PARTICLE, Kind.ASK_END)]
    if any(lex.kind is Kind.SCENE for lex in lexes):
        return _match_scene(lexes, norm)
    return _match_device(lexes, context, home, vocab)


def _match_scene(lexes: Sequence[Lex], norm: NormalizedText) -> MatchResult:
    if any(lex.kind not in _SCENE_ALLOWED for lex in lexes):
        return NoMatch()
    scenes = [lex for lex in lexes if lex.kind is Kind.SCENE]
    others = [lex for lex in lexes if lex.kind is not Kind.SCENE]
    # 장면 이름만 말한 "굿나잇"은 인사일 수 있어 실행 단서가 있을 때만 해석한다.
    if len(scenes) != 1 or not (others or norm.requested):
        return NoMatch()
    ids = scenes[0].ids
    if len(ids) > 1:
        return Clarify(reason=ClarifyReason.AMBIGUOUS_TARGET, candidate_entity_ids=ids)
    return SceneCommand(entity_id=ids[0])


def _allowed_domains(verb: Kind | None, has_brightness: bool) -> frozenset[str] | None:
    if verb in (Kind.ON, Kind.OFF, Kind.TOGGLE):
        return _SWITCHABLE
    if verb in (Kind.OPEN, Kind.CLOSE):
        return frozenset({"cover"})
    if verb in (Kind.UP, Kind.DOWN):
        return frozenset({"light", "cover"})
    if verb in (Kind.BRIGHTER, Kind.DARKER, Kind.SET) or has_brightness:
        return frozenset({"light"})
    return None


def _match_device(
    lexes: Sequence[Lex], context: UtteranceContext, home: HomeModel, vocab: Vocab
) -> MatchResult:
    areas: set[str] = set()
    groups: list[_Group] = []
    here = global_ = all_ = False
    for lex in lexes:
        if lex.kind is Kind.AREA:
            areas.update(lex.ids)
        elif lex.kind is Kind.HERE:
            here = True
        elif lex.kind is Kind.GLOBAL:
            global_ = all_ = True
        elif lex.kind is Kind.ALL:
            all_ = True
        elif lex.kind is Kind.DOMAIN and lex.domain is not None:
            groups.append(_Group(lex.surface, domain=lex.domain, hint=lex.hint))
        elif lex.kind is Kind.ENTITY:
            groups.append(_Group(lex.surface, ids=frozenset(lex.ids)))

    verbs = [lex for lex in lexes if lex.kind in VERB_KINDS]
    queries = [lex for lex in lexes if lex.kind in QUERY_KINDS]
    asks = [lex for lex in lexes if lex.kind is Kind.ASK]
    if len(verbs) > 1 or (verbs and (queries or asks)):
        return NoMatch()
    pool = [e for e in home.entities if e.domain != "scene"]
    explicit = set(areas)
    if here and context.hub_area_id is not None:
        explicit.add(context.hub_area_id)
    scope = _Scope(explicit, context.hub_area_id, all_ or global_)

    if queries or asks:
        return _match_query(lexes, groups, queries, asks, pool, vocab, scope)
    return _match_action(lexes, verbs[0].kind if verbs else None, groups, pool, vocab, scope)


@dataclass(frozen=True, slots=True)
class _Scope:
    explicit_areas: set[str]
    hub_area_id: str | None
    all_: bool


def _match_query(
    lexes: Sequence[Lex],
    groups: list[_Group],
    queries: list[Lex],
    asks: list[Lex],
    pool: list[EntityRef],
    vocab: Vocab,
    scope: _Scope,
) -> MatchResult:
    if not groups or any(lex.kind in (Kind.PCT, Kind.NUM) for lex in lexes):
        return NoMatch()
    domains = {g.domain for g in groups}
    if not queries and not domains <= {"sensor", None}:
        return NoMatch()
    allowed: frozenset[str] = frozenset({"sensor"})
    for query in queries:
        if query.kind in (Kind.Q_ON, Kind.Q_OFF):
            allowed = _SWITCHABLE
        elif query.kind in (Kind.Q_OPEN, Kind.Q_CLOSED):
            allowed = frozenset({"cover"})
        else:
            allowed = _SWITCHABLE | {"cover", "sensor"}
    if any(g.domain is not None and g.domain not in allowed for g in groups):
        return NoMatch()
    resolved = _resolve(groups, pool, vocab, allowed, scope, write=False)
    if resolved is None or isinstance(resolved, Clarify):
        return NoMatch()
    return StateQuery(entity_ids=resolved)


def _match_action(
    lexes: Sequence[Lex],
    verb: Kind | None,
    groups: list[_Group],
    pool: list[EntityRef],
    vocab: Vocab,
    scope: _Scope,
) -> MatchResult:
    kinds = {lex.kind for lex in lexes}
    has_brightness = bool(kinds & _BRIGHTNESS_KINDS)
    allowed = _allowed_domains(verb, has_brightness)
    if allowed is None:
        return NoMatch()
    brightness = _brightness(lexes, verb)
    if brightness is None or kinds & {Kind.SCENE_WORD, Kind.SCENE_VERB}:
        return NoMatch()
    if any(g.domain is not None and g.domain not in allowed for g in groups):
        return NoMatch()
    if not groups:
        groups = _default_groups(verb, scope, Kind.BRIGHTNESS in kinds or has_brightness)
        if not groups:
            return NoMatch()

    resolved = _resolve(groups, pool, vocab, allowed, scope, write=True)
    if resolved is None:
        return NoMatch()
    entity_ids = resolved.candidate_entity_ids if isinstance(resolved, Clarify) else resolved
    by_id = {e.entity_id: e for e in pool}
    domains = {by_id[i].domain for i in entity_ids}
    action = _derive_action(verb, brightness, domains, Kind.BRIGHTNESS in kinds)
    if action is None:
        return NoMatch()
    if isinstance(resolved, Clarify):
        return Clarify(
            reason=resolved.reason,
            candidate_entity_ids=resolved.candidate_entity_ids,
            action=action[0],
            params=action[1],
        )
    return DeviceCommand(entity_ids=entity_ids, action=action[0], params=action[1])


def _default_groups(verb: Kind | None, scope: _Scope, brightness_hint: bool) -> list[_Group]:
    """기기 지시어가 없을 때의 기본 대상. "다 꺼줘"는 조명·플러그·선풍기, 밝기 말은 조명."""
    if verb in (Kind.ON, Kind.OFF, Kind.TOGGLE):
        return [_Group("", domain=d) for d in _DEFAULT_ALL_DOMAINS] if scope.all_ else []
    if verb in (Kind.UP, Kind.DOWN):
        return [_Group("", domain="light")] if brightness_hint else []
    if verb in (Kind.BRIGHTER, Kind.DARKER, Kind.SET) or brightness_hint:
        return [_Group("", domain="light")]
    return []


def _brightness(lexes: Sequence[Lex], verb: Kind | None) -> tuple[int | None, int | None] | None:
    """(절대 %, 상대 %p). 밝기 의도가 없으면 (None, None), 해석 불가면 None."""
    kinds = {lex.kind for lex in lexes}
    numbers = [lex.number for lex in lexes if lex.kind in (Kind.PCT, Kind.NUM)]
    if Kind.NUM in kinds and Kind.BRIGHTNESS not in kinds:
        return None
    absolute: int | None = None
    if numbers:
        value = numbers[0]
        if len(numbers) > 1 or value is None or not 1 <= value <= 100:
            return None
        absolute = value
    elif Kind.MAX in kinds:
        absolute = _MAX_BRIGHTNESS_PCT
    elif Kind.MIN in kinds:
        absolute = _MIN_BRIGHTNESS_PCT
    superlative = Kind.SUPER in kinds
    if verb in (Kind.BRIGHTER, Kind.DARKER):
        if absolute is not None:
            return None
        brighter = verb is Kind.BRIGHTER
        if superlative:
            return (_MAX_BRIGHTNESS_PCT if brighter else _MIN_BRIGHTNESS_PCT), None
        return None, RELATIVE_BRIGHTNESS_STEP if brighter else -RELATIVE_BRIGHTNESS_STEP
    if superlative or (absolute is not None and verb not in (None, Kind.ON, Kind.SET)):
        return None
    if verb in (Kind.UP, Kind.DOWN):
        if Kind.BRIGHTNESS not in kinds:
            return None, None
        return None, RELATIVE_BRIGHTNESS_STEP if verb is Kind.UP else -RELATIVE_BRIGHTNESS_STEP
    if verb is Kind.SET and absolute is None:
        return None
    return absolute, None


def _derive_action(
    verb: Kind | None,
    brightness: tuple[int | None, int | None] | None,
    domains: set[str],
    brightness_word: bool,
) -> tuple[DeviceAction, CommandParams] | None:
    absolute, delta = brightness if brightness is not None else (None, None)
    has_brightness = absolute is not None or delta is not None
    if has_brightness:
        if domains != {"light"}:
            return None
        if verb in (Kind.UP, Kind.DOWN) and not brightness_word:
            return None
        return DeviceAction.SET_BRIGHTNESS, CommandParams(
            brightness_pct=absolute, brightness_delta_pct=delta
        )
    if verb in (Kind.UP, Kind.DOWN):
        if domains != {"cover"}:
            return None
        return (
            DeviceAction.TURN_ON if verb is Kind.UP else DeviceAction.TURN_OFF,
            CommandParams(),
        )
    actions = {
        Kind.ON: DeviceAction.TURN_ON,
        Kind.OPEN: DeviceAction.TURN_ON,
        Kind.OFF: DeviceAction.TURN_OFF,
        Kind.CLOSE: DeviceAction.TURN_OFF,
        Kind.TOGGLE: DeviceAction.TOGGLE,
    }
    action = actions.get(verb) if verb is not None else None
    return (action, CommandParams()) if action is not None else None


def _collect(
    groups: Sequence[_Group],
    scope_entities: Sequence[EntityRef],
    vocab: Vocab,
    allowed: frozenset[str] | set[str],
    per_area: bool,
) -> tuple[list[str], bool]:
    """범위 안에서 지시어에 맞는 엔티티를 모은다. (id 목록, 이름으로 특정했는데 여럿인지)."""
    found: set[str] = set()
    ambiguous = False
    for group in groups:
        if group.ids is not None:
            matched = [
                e for e in scope_entities if e.entity_id in group.ids and e.domain in allowed
            ]
            specific = True
        else:
            matched = [e for e in scope_entities if e.domain == group.domain]
            specific = False
            if group.hint is not None:
                matched = [e for e in matched if _has_term(vocab, e, group.hint)]
                specific = True
            elif group.word:
                named = [e for e in matched if _has_term(vocab, e, group.word)]
                if named:
                    matched, specific = named, True
        found.update(e.entity_id for e in matched)
        if specific:
            keys = Counter(e.area_id if per_area else None for e in matched)
            ambiguous = ambiguous or any(count > 1 for count in keys.values())
    ordered = [e.entity_id for e in scope_entities if e.entity_id in found]
    return ordered, ambiguous


def _has_term(vocab: Vocab, entity: EntityRef, term: str) -> bool:
    return any(term in t for t in vocab.terms.get(entity.entity_id, ()))


def _resolve(
    groups: Sequence[_Group],
    pool: Sequence[EntityRef],
    vocab: Vocab,
    allowed: frozenset[str] | set[str],
    scope: _Scope,
    *,
    write: bool,
) -> tuple[str, ...] | Clarify | None:
    """방 인지 규칙: 명시한 방 > 허브의 방 > 집 전체에서 유일한 대상. 여럿이면 되묻는다."""
    if scope.explicit_areas:
        in_areas = [e for e in pool if e.area_id in scope.explicit_areas]
        ids, ambiguous = _collect(groups, in_areas, vocab, allowed, per_area=True)
        return _finish(ids, ambiguous and not scope.all_ and write)
    if scope.all_:
        ids, _ = _collect(groups, pool, vocab, allowed, per_area=False)
        if write and len(ids) > 1:
            return Clarify(reason=ClarifyReason.BROAD_ACTION, candidate_entity_ids=tuple(ids))
        return tuple(ids) if ids else None
    if scope.hub_area_id is not None:
        in_hub = [e for e in pool if e.area_id == scope.hub_area_id]
        ids, ambiguous = _collect(groups, in_hub, vocab, allowed, per_area=False)
        if ids:
            return _finish(ids, ambiguous and write)
    ids, _ = _collect(groups, pool, vocab, allowed, per_area=False)
    if len(ids) <= 1 or not write:
        return tuple(ids) if ids else None
    return Clarify(reason=ClarifyReason.AMBIGUOUS_TARGET, candidate_entity_ids=tuple(ids))


def _finish(ids: list[str], ambiguous: bool) -> tuple[str, ...] | Clarify | None:
    if not ids:
        return None
    if ambiguous:
        return Clarify(reason=ClarifyReason.AMBIGUOUS_TARGET, candidate_entity_ids=tuple(ids))
    return tuple(ids)
