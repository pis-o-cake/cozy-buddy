"""LLM 툴 루프. 결정론으로 풀지 못한 발화를 스트리밍으로 답한다."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from app.config import Settings
from app.core.i18n import Translator
from app.core.openai_gateway import OpenAIGateway, OpenAIGatewayError, ToolCall
from app.domain.agent.context import TurnContext
from app.domain.agent.memory import ConversationMemory
from app.domain.agent.skills.registry import SkillRegistry, tool_message
from app.domain.telemetry.turn import Stage

MAX_LLM_ITERATIONS = 5


class LlmStep:
    """툴 목록은 스킬에서 만들고 툴 호출은 스킬로 실행한다.

    규칙: `role:"tool"` 로 결과를 돌려주고, 최대 5회 돌며, 마지막 턴은 툴 없이 불러 텍스트로 끝낸다.
    """

    def __init__(
        self,
        settings: Settings,
        translate: Translator,
        openai: OpenAIGateway | None,
        skills: SkillRegistry,
        memory: ConversationMemory,
    ) -> None:
        self._settings = settings
        self._t = translate
        self._openai = openai
        self._skills = skills
        self._memory = memory

    async def stream(self, text: str, ctx: TurnContext) -> AsyncIterator[str]:
        """응답 텍스트 조각을 흘린다. 시간 초과·게이트웨이 오류는 사과 문장으로 끝낸다."""
        if self._openai is None:
            yield self._t("voice.unavailable")
            return
        tools = self._skills.tools(ctx.view)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(ctx)},
            *self._history_messages(ctx),
            {"role": "user", "content": text},
        ]
        turn = ctx.turn
        emitted = False
        try:
            for iteration in range(MAX_LLM_ITERATIONS):
                # 마지막 턴은 툴 없이 불러 반드시 텍스트로 끝나게 한다.
                is_last = iteration == MAX_LLM_ITERATIONS - 1
                content = ""
                calls: tuple[ToolCall, ...] = ()
                stream = self._openai.stream_chat(messages, None if is_last else tools)
                # 이해 시간에는 LLM 을 기다린 시간만 센다. 소비자가 조각을 처리하는 시간은 뺀다.
                waited = turn.clock()
                async for chunk in self._with_idle_timeout(stream):
                    turn.add_seconds(Stage.UNDERSTAND, turn.clock() - waited)
                    if chunk.text:
                        emitted = True
                        content += chunk.text
                        yield chunk.text
                    calls = chunk.tool_calls or calls
                    waited = turn.clock()
                turn.add_seconds(Stage.UNDERSTAND, turn.clock() - waited)
                if not calls:
                    return
                messages.append(_assistant_message(content, calls))
                for call in calls:
                    with turn.timed(Stage.ACT):
                        result = await self._skills.run_tool(
                            call.name, call.parsed_arguments(), ctx
                        )
                    turn.llm_tool_calls += 1
                    turn.record_skill(result.ok)
                    logger.debug("Tool executed: {} ok={}", call.name, result.ok)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": tool_message(result)}
                    )
        except (TimeoutError, OpenAIGatewayError) as exc:
            logger.warning("LLM fallback failed: {}", exc or type(exc).__name__)
            turn.fail("llm_timeout" if isinstance(exc, TimeoutError) else "llm_error")
            if not emitted:
                yield self._t("agent.llm.apology")

    def _history_messages(self, ctx: TurnContext) -> list[dict[str, Any]]:
        """최근 대화를 사용자·어시스턴트 메시지 쌍으로 만든다. 지난 턴의 툴 메시지는 싣지 않는다."""
        messages: list[dict[str, Any]] = []
        for past in self._memory.recent_turns(ctx.hub_id):
            messages.append({"role": "user", "content": past.user})
            messages.append({"role": "assistant", "content": past.assistant})
        return messages

    def _system_prompt(self, ctx: TurnContext) -> str:
        weekday = self._t(f"agent.day.{ctx.now.isoweekday()}")
        context = self._t("agent.llm.context").format(
            area=ctx.view.area_name(ctx.area_id) or self._t("agent.llm.area_unknown"),
            now=f"{ctx.now:%Y-%m-%d %H:%M} {weekday}",
        )
        return f"{self._t('agent.llm.system')}\n{context}"

    async def _with_idle_timeout[T](self, stream: AsyncIterator[T]) -> AsyncIterator[T]:
        """조각 사이 대기가 llm_timeout_seconds 를 넘으면 TimeoutError 를 낸다."""
        timeout = self._settings.llm_timeout_seconds
        iterator = aiter(stream)
        try:
            while True:
                try:
                    async with asyncio.timeout(timeout):
                        item = await anext(iterator)
                except StopAsyncIteration:
                    return
                yield item
        finally:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()


def _assistant_message(content: str, calls: tuple[ToolCall, ...]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in calls
        ],
    }
