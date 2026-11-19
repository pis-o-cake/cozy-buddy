"""결정론 시도: 결정론 매처와 날씨 의도."""

from app.domain.agent.context import TurnContext
from app.domain.agent.matcher import match
from app.domain.agent.model import Intent, NoMatch, UtteranceContext, WeatherIntent
from app.domain.agent.normalize import normalize
from app.domain.agent.understand.understander import Step
from app.domain.agent.weather_intent import match_weather


class MatcherAttempt:
    """기기·장면·상태·알람 발화를 규칙으로 해석한다. 날씨는 다음 시도가 맡는다."""

    step = Step.MATCHER

    async def __call__(self, text: str, ctx: TurnContext) -> Intent | None:
        utterance = UtteranceContext(text, ctx.area_id or None, ctx.now)
        result = match(utterance, ctx.view.model, include_weather=False)
        return None if isinstance(result, NoMatch) else result


class WeatherAttempt:
    """기기 매칭이 실패한 발화 중 바깥 날씨 질문을 해석한다."""

    step = Step.WEATHER

    async def __call__(self, text: str, ctx: TurnContext) -> WeatherIntent | None:
        norm = normalize(text)
        if not norm.compact:
            return None
        return match_weather(norm, text)
