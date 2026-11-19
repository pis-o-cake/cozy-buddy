"""맥락 해석: 앞선 대화에 기대는 발화를 의도로 푼다."""

from collections.abc import Sequence
from typing import Protocol

from app.domain.agent.clarify import ClarifyStore
from app.domain.agent.context import TurnContext
from app.domain.agent.followup import Answer, classify_answer, narrow_candidates
from app.domain.agent.model import (
    CancelAllAlarms,
    CancelSelected,
    Clarify,
    ClarifyReason,
    CommandParams,
    DeviceCommand,
    Dismiss,
    EntityRef,
    Intent,
    SceneCommand,
    StateQuery,
)
from app.domain.agent.skills.alarm_select import AlarmSelector
from app.domain.agent.skills.alarm_store import AlarmStore
from app.domain.agent.understand.understander import Step


class ContextResolver(Protocol):
    """맥락 해석기 하나. 자기 몫이 아니면 None 을 돌려 다음 해석기로 넘긴다."""

    async def resolve(self, text: str, ctx: TurnContext) -> Intent | None:
        """앞선 대화를 참고해 발화를 의도로 푼다."""
        ...


class ContextAttempt:
    """맥락 해석기를 순서대로 돌린다.

    확인 질문 답(ClarifyAnswerResolver)이 먼저다. 보류 질문이 있으면 그 답으로 읽는 것이 자연스럽다.
    그 뒤에 직전 턴 대상에 대한 후속 지시(FollowupResolver)가 온다.
    """

    step = Step.CONTEXT

    def __init__(self, resolvers: Sequence[ContextResolver]) -> None:
        self._resolvers = tuple(resolvers)

    async def __call__(self, text: str, ctx: TurnContext) -> Intent | None:
        for resolver in self._resolvers:
            intent = await resolver.resolve(text, ctx)
            if intent is not None:
                return intent
        return None


class ClarifyAnswerResolver:
    """보류 중인 확인 질문("어떤 거요?", "정말 진행할까요?")에 대한 답을 해석한다.

    보류 질문은 답이든 아니든 한 번 꺼내면 사라진다. 답이 아니면 None 을 돌려 일반 발화로 처리한다.
    """

    def __init__(self, store: ClarifyStore, alarms: AlarmStore, selector: AlarmSelector) -> None:
        self._store = store
        self._alarms = alarms
        self._selector = selector

    async def resolve(self, text: str, ctx: TurnContext) -> Intent | None:
        clarify = self._store.take(ctx.conversation_key)
        if clarify is None:
            return None
        answer = classify_answer(text)
        if answer is Answer.NO:
            return Dismiss()
        if clarify.reason is ClarifyReason.AMBIGUOUS_ALARM:
            return await self._pick_alarm(clarify, answer, text, ctx)
        if clarify.reason is ClarifyReason.BROAD_ACTION:
            return _confirm_broad(clarify, answer)
        return _pick_candidate(clarify, answer, text, ctx)

    async def _pick_alarm(
        self, clarify: Clarify, answer: Answer | None, text: str, ctx: TurnContext
    ) -> Intent | None:
        wanted = set(clarify.candidate_alarm_ids)
        rows = [r for r in await self._alarms.live_rows(ctx.hub_id) if r.alarm_id in wanted]
        if not rows:
            return CancelSelected(())
        chosen = self._selector.choose(rows, answer, text, ctx.now)
        if chosen is None:
            return None
        return CancelSelected(tuple(r.alarm_id for r in chosen), confirmed=answer is Answer.ALL)


def _confirm_broad(clarify: Clarify, answer: Answer | None) -> Intent | None:
    if answer not in (Answer.YES, Answer.ALL):
        return None
    if clarify.action is None:
        return CancelAllAlarms()
    return DeviceCommand(
        clarify.candidate_entity_ids, clarify.action, clarify.params or CommandParams()
    )


def _pick_candidate(
    clarify: Clarify, answer: Answer | None, text: str, ctx: TurnContext
) -> Intent | None:
    candidates = [e for i in clarify.candidate_entity_ids if (e := ctx.view.entity(i))]
    if answer is Answer.YES:
        # "응" 만으로는 고를 수 없다. 같은 질문을 다시 한다.
        return clarify
    if answer is Answer.ALL:
        chosen: tuple[EntityRef, ...] = tuple(candidates)
    else:
        narrowed = narrow_candidates(text, candidates, ctx.view.model)
        if not narrowed.entities or not narrowed.changed:
            return None
        chosen = narrowed.entities
    if len(chosen) > 1 and answer is not Answer.ALL:
        return Clarify(
            reason=clarify.reason,
            candidate_entity_ids=tuple(e.entity_id for e in chosen),
            action=clarify.action,
            params=clarify.params,
        )
    return _resolved(clarify, chosen)


def _resolved(clarify: Clarify, chosen: tuple[EntityRef, ...]) -> Intent:
    """확인이 끝난 후보로 원래 의도했던 명령을 만든다."""
    ids = tuple(e.entity_id for e in chosen)
    if clarify.action is not None:
        return DeviceCommand(ids, clarify.action, clarify.params or CommandParams())
    if chosen[0].domain == "scene":
        return SceneCommand(ids[0])
    return StateQuery(ids)
