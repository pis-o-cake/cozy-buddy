"""울리는 알람을 음성으로 끄거나 미루는 스킬."""

from cozy.v1.alarm_pb2 import (
    ALARM_CONTROL_ACTION_SNOOZE,
    ALARM_CONTROL_ACTION_STOP,
    AlarmControlAction,
)
from loguru import logger

from app.core.hub_control import HubControl
from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.model import AlarmControlIntent, AlarmControlKind, Intent
from app.domain.agent.skills.base import KEY_INTERNAL, BaseSkill, SkillResult

KEY_STOPPED = "agent.alarm_control.stopped"
KEY_SNOOZED = "agent.alarm_control.snoozed"
KEY_NOT_RINGING = "agent.alarm_control.not_ringing"
KEY_FAILED = "agent.alarm_control.failed"

# 허브 기본 스누즈와 같다. 말한 분이 없으면 이 값을 명시해 보내 응답 문구와 동작을 맞춘다.
DEFAULT_SNOOZE_MINUTES = 9


class AlarmControlSkill(BaseSkill):
    """AlarmControlIntent. 허브에 AlarmControl 을 보내고 결과를 받은 뒤에 답한다."""

    intents = (AlarmControlIntent,)

    def __init__(self, translate: Translator, hub_control: HubControl) -> None:
        super().__init__(translate)
        self._hub = hub_control

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        if not isinstance(intent, AlarmControlIntent):
            return self.failed(KEY_INTERNAL)
        alarm_id = self._hub.ringing_alarm_id(ctx.hub_id) if ctx.hub_id else ""
        if not alarm_id:
            return self.failed(KEY_NOT_RINGING)
        stop = intent.kind is AlarmControlKind.STOP
        minutes = intent.snooze_minutes or DEFAULT_SNOOZE_MINUTES
        action: AlarmControlAction = (
            ALARM_CONTROL_ACTION_STOP if stop else ALARM_CONTROL_ACTION_SNOOZE
        )
        result = await self._hub.alarm_control(ctx.hub_id, alarm_id, action, 0 if stop else minutes)
        if result is None or not result.ok:
            logger.warning(
                "Alarm control failed: hub={} kind={} ok={}",
                ctx.hub_id,
                intent.kind,
                None if result is None else result.ok,
            )
            return self.failed(KEY_FAILED)
        if stop:
            return SkillResult(True, KEY_STOPPED, text=self._t(KEY_STOPPED))
        args = {"minutes": str(minutes)}
        return SkillResult(True, KEY_SNOOZED, args, text=self._t(KEY_SNOOZED).format(**args))
