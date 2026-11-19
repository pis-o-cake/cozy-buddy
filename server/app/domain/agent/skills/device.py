"""기기 제어·장면 실행 스킬."""

import asyncio
import uuid
from typing import Any

from cozy.v1.home_pb2 import EntityCommand
from loguru import logger

from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import HomeView
from app.domain.agent.model import (
    CommandParams,
    DeviceAction,
    DeviceCommand,
    Intent,
    SceneCommand,
)
from app.domain.agent.skills.base import (
    KEY_INTERNAL,
    KEY_NOT_FOUND,
    BaseSkill,
    SkillResult,
    optional_int,
)
from app.domain.agent.skills.tooling import catalog, function_tool
from app.domain.home.service import HomeService

TOOL_CONTROL = "control_device"
CONTROL_ACTIONS = ("turn_on", "turn_off", "toggle", "set_brightness", "activate")
CONTROLLABLE_DOMAINS = frozenset(
    {"light", "switch", "fan", "cover", "climate", "media_player", "scene", "script"}
)
MIN_BRIGHTNESS_PCT = 1
MAX_BRIGHTNESS_PCT = 100


class DeviceSkill(BaseSkill):
    """DeviceCommand, SceneCommand 와 control_device 툴. 둘 다 `_apply` 한 곳으로 모인다."""

    intents = (DeviceCommand, SceneCommand)
    tool_names = frozenset({TOOL_CONTROL})

    def __init__(self, translate: Translator, home: HomeService) -> None:
        super().__init__(translate)
        self._home = home

    def tool_specs(self, view: HomeView) -> list[dict[str, Any]]:
        ids, entries = catalog(view, CONTROLLABLE_DOMAINS)
        if not ids:
            return []
        return [
            function_tool(
                TOOL_CONTROL,
                f"Control a device or run a scene. Entities: {entries}",
                {
                    "entity_id": {"type": "string", "enum": ids},
                    "action": {"type": "string", "enum": list(CONTROL_ACTIONS)},
                    "brightness_pct": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Absolute brightness for set_brightness.",
                    },
                    "brightness_delta_pct": {
                        "type": "integer",
                        "minimum": -100,
                        "maximum": 100,
                        "description": "Relative brightness change for set_brightness.",
                    },
                },
                ["entity_id", "action"],
            )
        ]

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        ids: tuple[str, ...]
        if isinstance(intent, SceneCommand):
            ids, action, pct, delta = (intent.entity_id,), "activate", None, None
        elif isinstance(intent, DeviceCommand):
            params = intent.params
            ids = intent.entity_ids
            action, pct, delta = (
                intent.action.value,
                params.brightness_pct,
                params.brightness_delta_pct,
            )
        else:
            return self.failed(KEY_INTERNAL)
        error_key = await self._apply(ctx.view, ids, action, pct, delta)
        if error_key:
            return self.failed(error_key, ids)
        return self.templated(intent, ctx, ids)

    async def run_tool(self, name: str, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        entity_id = str(args["entity_id"])
        action = str(args["action"])
        if ctx.view.entity(entity_id) is None or action not in CONTROL_ACTIONS:
            raise ValueError("unknown entity or action")
        pct = optional_int(args, "brightness_pct", 0, 100)
        delta = optional_int(args, "brightness_delta_pct", -100, 100)
        if action == DeviceAction.SET_BRIGHTNESS and pct is None and delta is None:
            raise ValueError("brightness required")
        error_key = await self._apply(ctx.view, (entity_id,), action, pct, delta)
        if error_key:
            return self.failed(error_key, (entity_id,))
        return SkillResult(
            True,
            entities=(entity_id,),
            payload={"ok": True},
            remember=_remembered_intent(entity_id, action, pct, delta),
        )

    async def _apply(
        self,
        view: HomeView,
        entity_ids: tuple[str, ...],
        action: str,
        brightness_pct: int | None,
        brightness_delta_pct: int | None,
    ) -> str | None:
        """엔티티들에 명령을 보낸다. 실패하면 사용자 메시지 키를, 성공하면 None 을 돌려준다."""
        commands: list[EntityCommand] = []
        for entity_id in entity_ids:
            entity = view.entity(entity_id)
            if entity is None:
                return KEY_NOT_FOUND
            params: dict[str, str] = {}
            if action == DeviceAction.SET_BRIGHTNESS:
                target = _target_brightness(view, entity_id, brightness_pct, brightness_delta_pct)
                params["brightness_pct"] = str(target)
            commands.append(
                EntityCommand(
                    request_id=uuid.uuid4().hex,
                    entity_id=entity_id,
                    action=_ha_action(entity.domain, action),
                    params=params,
                )
            )
        results = await asyncio.gather(*(self._home.execute(c) for c in commands))
        failed = next((r for r in results if not r.ok), None)
        if failed is None:
            return None
        logger.warning("Device command failed: code={} key={}", failed.code, failed.message_key)
        return failed.message_key or KEY_INTERNAL


def _remembered_intent(entity_id: str, action: str, pct: int | None, delta: int | None) -> Intent:
    """툴 호출을 결정론 경로가 기억하는 것과 같은 의도로 바꾼다."""
    if action == "activate":
        return SceneCommand(entity_id)
    return DeviceCommand((entity_id,), DeviceAction(action), CommandParams(pct, delta))


def _ha_action(domain: str, action: str) -> str:
    if action == "activate" and domain == "script":
        return "run"
    return action


def _target_brightness(
    view: HomeView, entity_id: str, absolute: int | None, delta: int | None
) -> int:
    """절대값은 그대로(0 은 끄기), 상대값은 현재 밝기에 더해 1~100 으로 자른다."""
    if absolute is not None:
        return max(0, min(MAX_BRIGHTNESS_PCT, absolute))
    if delta is None:
        raise ValueError("brightness required")
    current = view.brightness_pct(entity_id)
    return max(MIN_BRIGHTNESS_PCT, min(MAX_BRIGHTNESS_PCT, current + delta))
