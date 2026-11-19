"""스킬 공통 계약. 결정론 의도와 LLM 툴 호출이 같은 스킬 구현으로 들어온다."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import HomeView
from app.domain.agent.model import Intent
from app.domain.agent.responses import build_response, render

KEY_INTERNAL = "error.internal"
KEY_NOT_FOUND = "home.entity_not_found"


@dataclass(frozen=True, slots=True)
class SkillResult:
    """스킬 실행 결과.

    Attributes:
        ok: 요청한 일을 해냈는지.
        response_key: 응답 문구의 언어팩 키.
        args: 언어팩 문구에 채운 인자.
        entities: 영향을 받은 엔티티 ID. 기기 스킬이 채운다.
        text: 사용자에게 읽어 줄 완성된 문장.
        payload: LLM 툴 호출 결과로 돌려줄 JSON 객체. 결정론 실행에서는 비어 있다.
    """

    ok: bool
    response_key: str = ""
    args: Mapping[str, str] = field(default_factory=dict)
    entities: tuple[str, ...] = ()
    text: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


class BaseSkill:
    """스킬 기본 구현. 서브클래스는 intents, tool_names 와 필요한 메서드만 채운다.

    Attributes:
        intents: execute 가 처리하는 의도 타입.
        tool_names: run_tool 이 처리하는 LLM 툴 이름.
    """

    intents: tuple[type, ...] = ()
    tool_names: frozenset[str] = frozenset()

    def __init__(self, translate: Translator) -> None:
        self._t = translate

    def tool_specs(self, view: HomeView) -> list[dict[str, Any]]:
        """현재 집에 맞춘 LLM 툴 정의. 노출할 툴이 없으면 빈 목록."""
        return []

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        """결정론 의도를 실행한다."""
        raise NotImplementedError

    async def run_tool(self, name: str, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        """LLM 툴 호출을 실행한다. 인자가 잘못되면 ValueError/KeyError/TypeError 를 낸다."""
        raise NotImplementedError

    def failed(self, key: str, entities: tuple[str, ...] = ()) -> SkillResult:
        """사용자용 메시지 키로 실패 결과를 만든다. 툴 결과에는 번역한 문구를 싣는다."""
        text = self._t(key)
        return SkillResult(
            False, key, entities=entities, text=text, payload={"ok": False, "error": text}
        )

    def templated(
        self, intent: Intent, ctx: TurnContext, entities: tuple[str, ...] = ()
    ) -> SkillResult:
        """의도에 맞는 템플릿 문구로 성공 결과를 만든다. 문구가 없으면 not_found 로 답한다."""
        response = build_response(intent, ctx.view.model, ctx.now, self._t)  # type: ignore[arg-type]
        if response is None:
            return SkillResult(False, KEY_NOT_FOUND, text=self._t(KEY_NOT_FOUND))
        text = render(response, self._t)
        return SkillResult(True, response.key, response.args, entities, text, {"ok": True})


def bounded(value: int, low: int, high: int) -> int:
    """low..high 범위 안의 값만 통과시킨다."""
    if not low <= value <= high:
        raise ValueError(f"{value} not in {low}..{high}")
    return value


def required_int(args: dict[str, Any], key: str, low: int, high: int) -> int:
    """필수 정수 인자."""
    return bounded(int(args[key]), low, high)


def optional_int(args: dict[str, Any], key: str, low: int, high: int) -> int | None:
    """선택 정수 인자. 없거나 null 이면 None."""
    value = args.get(key)
    return None if value is None else bounded(int(value), low, high)
