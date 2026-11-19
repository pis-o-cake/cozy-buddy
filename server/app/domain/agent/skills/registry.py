"""스킬 모음. 의도와 LLM 툴 호출을 알맞은 스킬로 보내고 LLM 툴 목록을 만든다."""

import json
from collections.abc import Sequence
from typing import Any

from loguru import logger

from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import HomeView
from app.domain.agent.model import Intent
from app.domain.agent.skills.base import KEY_INTERNAL, BaseSkill, SkillResult


class SkillRegistry:
    """스킬을 순서대로 보관한다. 툴 목록은 스킬 순서를 따른다."""

    def __init__(self, skills: Sequence[BaseSkill], translate: Translator) -> None:
        self._skills = tuple(skills)
        self._t = translate

    def tools(self, view: HomeView) -> list[dict[str, Any]]:
        """현재 집에 맞춘 LLM 툴 정의를 스킬에서 모아 만든다."""
        return [spec for skill in self._skills for spec in skill.tool_specs(view)]

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        """의도를 처리하는 스킬로 실행한다. 맡는 스킬이 없으면 내부 오류 결과를 돌려준다."""
        for skill in self._skills:
            if isinstance(intent, skill.intents):
                return await skill.execute(intent, ctx)
        logger.error("No skill handles intent: {}", type(intent).__name__)
        return SkillResult(False, KEY_INTERNAL, text=self._t(KEY_INTERNAL))

    async def run_tool(self, name: str, arguments: dict[str, Any], ctx: TurnContext) -> SkillResult:
        """LLM 툴 호출을 실행한다. 모르는 툴이나 잘못된 인자는 payload 에 오류로 담는다."""
        skill = next((s for s in self._skills if name in s.tool_names), None)
        if skill is None:
            return SkillResult(False, payload={"ok": False, "error": "unknown_tool"})
        try:
            return await skill.run_tool(name, arguments, ctx)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Invalid tool arguments: tool={} error={}", name, exc)
            return SkillResult(False, payload={"ok": False, "error": "invalid_arguments"})


def tool_message(result: SkillResult) -> str:
    """툴 결과를 LLM 에 돌려줄 JSON 문자열로 만든다."""
    return json.dumps(dict(result.payload), ensure_ascii=False)
