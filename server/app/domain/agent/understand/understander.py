"""이해 단계의 시도 순서. 처음 의도를 낸 시도에서 멈춘다."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.domain.agent.context import TurnContext
from app.domain.agent.model import Intent


class Step(StrEnum):
    """의도를 낸 이해 단계. 값은 턴 기록의 path 와 같다."""

    CONTEXT = "context"
    MATCHER = "matcher"
    WEATHER = "weather"
    LLM = "llm"


class Attempt(Protocol):
    """결정론 시도 하나. 의도를 해석하지 못하면 None 을 돌려 다음 시도로 넘긴다."""

    step: Step

    async def __call__(self, text: str, ctx: TurnContext) -> Intent | None:
        """텍스트를 의도로 해석한다."""
        ...


@dataclass(frozen=True, slots=True)
class Understanding:
    """이해 결과. intent 가 None 이면 결정론으로 풀지 못해 LLM 이 맡는다."""

    step: Step
    intent: Intent | None


class Understander:
    """결정론 시도를 순서대로 돌린다. LLM 은 스트리밍이라 별도 단계(LlmStep)가 맡는다.

    순서를 바꾸거나 시도를 끼워 넣을 때 고치는 곳은 생성자에 넘기는 목록 하나다.
    """

    def __init__(self, attempts: Sequence[Attempt]) -> None:
        self._attempts = tuple(attempts)

    async def understand(self, text: str, ctx: TurnContext) -> Understanding:
        """처음 의도를 낸 시도의 결과를 돌려준다. 모두 실패하면 LLM 몫(intent=None)이다."""
        for attempt in self._attempts:
            intent = await attempt(text, ctx)
            if intent is not None:
                return Understanding(attempt.step, intent)
        return Understanding(Step.LLM, None)
