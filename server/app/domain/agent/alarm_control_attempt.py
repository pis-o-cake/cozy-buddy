"""울리는 알람 제어를 해석하는 이해 시도."""

from collections.abc import Callable

from app.domain.agent.context import TurnContext
from app.domain.agent.matcher import match
from app.domain.agent.model import AlarmControlIntent, UtteranceContext
from app.domain.agent.understand.understander import Step


class AlarmControlAttempt:
    """허브가 알람을 울리는 중일 때만 정지·스누즈 발화를 해석한다.

    일반 매처 시도보다 앞에 둔다. 울리지 않는 허브의 발화는 건드리지 않으므로 그때의
    "알람 꺼"는 기존 매처가 다음 알람 취소로 해석한다.
    """

    step = Step.MATCHER

    def __init__(self, ringing_alarm_id: Callable[[str], str]) -> None:
        self._ringing_alarm_id = ringing_alarm_id

    async def __call__(self, text: str, ctx: TurnContext) -> AlarmControlIntent | None:
        if not ctx.hub_id or not self._ringing_alarm_id(ctx.hub_id):
            return None
        utterance = UtteranceContext(text, ctx.area_id or None, ctx.now, ringing=True)
        result = match(utterance, ctx.view.model, include_weather=False)
        return result if isinstance(result, AlarmControlIntent) else None
