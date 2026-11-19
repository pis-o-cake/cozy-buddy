"""기기 상태 조회 스킬."""

from typing import Any

from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import HomeView
from app.domain.agent.model import Intent, StateQuery
from app.domain.agent.responses import build_state_response, render
from app.domain.agent.skills.base import KEY_NOT_FOUND, BaseSkill, SkillResult
from app.domain.agent.skills.tooling import catalog, function_tool

TOOL_STATE = "get_device_state"


class StateSkill(BaseSkill):
    """StateQuery 와 get_device_state 툴. 둘 다 HomeView 의 같은 캐시를 읽는다."""

    intents = (StateQuery,)
    tool_names = frozenset({TOOL_STATE})

    def __init__(self, translate: Translator) -> None:
        super().__init__(translate)

    def tool_specs(self, view: HomeView) -> list[dict[str, Any]]:
        ids, entries = catalog(view, None)
        if not ids:
            return []
        return [
            function_tool(
                TOOL_STATE,
                f"Read the current state of an entity. Entities: {entries}",
                {"entity_id": {"type": "string", "enum": ids}},
                ["entity_id"],
            )
        ]

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        if not isinstance(intent, StateQuery):
            return self.failed(KEY_NOT_FOUND)
        answers: list[str] = []
        found: list[str] = []
        for entity_id in intent.entity_ids:
            status = ctx.view.status.get(entity_id)
            response = status and build_state_response(ctx.view.model, entity_id, status.state)
            if response:
                answers.append(render(response, self._t))
                found.append(entity_id)
        if not answers:
            return self.failed(KEY_NOT_FOUND, intent.entity_ids)
        return SkillResult(True, "agent.query", entities=tuple(found), text=". ".join(answers))

    async def run_tool(self, name: str, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        entity_id = str(args["entity_id"])
        entity = ctx.view.entity(entity_id)
        status = ctx.view.status.get(entity_id)
        if entity is None or status is None:
            raise ValueError("unknown entity")
        payload: dict[str, Any] = {
            "ok": True,
            "entity_id": entity_id,
            "name": entity.name,
            "area": ctx.view.area_name(entity.area_id),
            "state": status.state,
        }
        unit = status.attributes.get("unit_of_measurement")
        if unit:
            payload["unit"] = unit
        if entity.domain == "light" and status.state != "off":
            payload["brightness_pct"] = ctx.view.brightness_pct(entity_id)
        return SkillResult(True, entities=(entity_id,), payload=payload)
