"""바깥 날씨 스킬."""

from typing import Any

from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import HomeView
from app.domain.agent.model import Intent, WeatherIntent
from app.domain.agent.skills.base import KEY_INTERNAL, BaseSkill, SkillResult
from app.domain.agent.skills.tooling import function_tool
from app.domain.weather.schemas import WeatherWhen
from app.domain.weather.service import WeatherService

TOOL_WEATHER = "get_weather"
KEY_DISABLED = "weather.disabled"


class WeatherSkill(BaseSkill):
    """WeatherIntent 와 get_weather 툴. 둘 다 WeatherService 캐시에서 답한다."""

    intents = (WeatherIntent,)
    tool_names = frozenset({TOOL_WEATHER})

    def __init__(self, translate: Translator, weather: WeatherService | None) -> None:
        super().__init__(translate)
        self._weather = weather

    def tool_specs(self, view: HomeView) -> list[dict[str, Any]]:
        return [
            function_tool(
                TOOL_WEATHER,
                "Get the outdoor weather at the home location: current conditions, or the "
                "forecast for today or tomorrow.",
                {"when": {"type": "string", "enum": [w.value for w in WeatherWhen]}},
                ["when"],
            )
        ]

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        if not isinstance(intent, WeatherIntent):
            return self.failed(KEY_INTERNAL)
        if self._weather is None:
            return SkillResult(False, KEY_DISABLED, text=self._t(KEY_DISABLED))
        text = self._weather.answer(intent.when, self._t)
        return SkillResult(self._weather.current() is not None, "weather.answer", text=text)

    async def run_tool(self, name: str, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        if self._weather is None:
            return SkillResult(False, KEY_DISABLED, payload={"ok": False, "error": "disabled"})
        payload = self._weather.tool_payload(WeatherWhen(str(args["when"])), self._t)
        return SkillResult(bool(payload.get("ok")), payload=payload)
