"""다른 허브로 보내는 방송 스킬."""

from typing import Any

from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import HomeView
from app.domain.agent.model import BroadcastIntent, Intent
from app.domain.agent.skills.base import KEY_INTERNAL, BaseSkill, SkillResult
from app.domain.agent.skills.tooling import function_tool
from app.domain.broadcast.service import (
    ALL_TARGET,
    MAX_TEXT_CHARS,
    BroadcastReport,
    BroadcastRequest,
    BroadcastService,
)

TOOL_BROADCAST = "broadcast"
KEY_SENT = "broadcast.sent"
KEY_SENT_MANY = "broadcast.sent_many"
KEY_SENT_ALL = "broadcast.sent_all"
KEY_OFFLINE_NOTE = "broadcast.offline_note"
KEY_TEXT_ONLY_NOTE = "broadcast.text_only_note"
KEY_NO_TARGET = "broadcast.no_target"
KEY_NO_OTHER_HUBS = "broadcast.no_other_hubs"
KEY_ALL_OFFLINE = "broadcast.all_offline"


class BroadcastSkill(BaseSkill):
    """BroadcastIntent 와 broadcast 툴. 말한 허브를 뺀 대상 허브에 방송하고 결과를 답한다."""

    intents = (BroadcastIntent,)
    tool_names = frozenset({TOOL_BROADCAST})

    def __init__(self, translate: Translator, broadcast: BroadcastService) -> None:
        super().__init__(translate)
        self._broadcast = broadcast

    def tool_specs(self, view: HomeView) -> list[dict[str, Any]]:
        areas = ", ".join(area.name for area in view.model.areas)
        known = f" Known areas: {areas}." if areas else ""
        return [
            function_tool(
                TOOL_BROADCAST,
                "Announce a spoken message on other hubs in the home. Target is one area or "
                f'hub name, or "{ALL_TARGET}" for every hub except this one.{known} '
                "Write the message as a short polite Korean sentence to be read aloud.",
                {
                    "target": {"type": "string"},
                    "message": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                },
                ["target", "message"],
            )
        ]

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        if not isinstance(intent, BroadcastIntent):
            return self.failed(KEY_INTERNAL)
        return await self._run(intent.target, intent.text, ctx)

    async def run_tool(self, name: str, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        text = str(args["message"]).strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            raise ValueError("message length out of range")
        target = str(args["target"]).strip()
        result = await self._run(None if target.lower() == ALL_TARGET else target, text, ctx)
        payload = {"ok": result.ok, "reply": result.text}
        return SkillResult(
            result.ok, result.response_key, result.args, text=result.text, payload=payload
        )

    async def _run(self, target: str | None, text: str, ctx: TurnContext) -> SkillResult:
        request = BroadcastRequest(
            target=target,
            text=text,
            from_hub_id=ctx.hub_id,
            from_area_id=ctx.area_id,
            from_area_name=ctx.view.area_name(ctx.area_id),
            areas=ctx.view.model.areas,
        )
        return self._result(await self._broadcast.send(request))

    def _result(self, report: BroadcastReport) -> SkillResult:
        args = {"target": report.label}
        if report.matched == 0:
            key = KEY_NO_OTHER_HUBS if report.everyone else KEY_NO_TARGET
            return self._failure(key, args)
        if not report.delivered:
            return self._failure(KEY_ALL_OFFLINE, args)
        count = len(report.delivered)
        key = _sent_key(report, count)
        sentences = [self._t(key).format(count=count, **args)]
        if report.offline:
            sentences.append(self._t(KEY_OFFLINE_NOTE).format(offline=len(report.offline)))
        if not report.audio:
            sentences.append(self._t(KEY_TEXT_ONLY_NOTE))
        return SkillResult(True, key, args, text=" ".join(sentences))

    def _failure(self, key: str, args: dict[str, str]) -> SkillResult:
        text = self._t(key).format(**args)
        return SkillResult(False, key, args, text=text, payload={"ok": False, "error": text})


def _sent_key(report: BroadcastReport, count: int) -> str:
    if report.everyone:
        return KEY_SENT_ALL
    return KEY_SENT if count == 1 else KEY_SENT_MANY
