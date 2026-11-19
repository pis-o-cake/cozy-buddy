"""대화 에이전트: 이해(맥락·매처·날씨) -> 실행(스킬), 풀리지 않으면 LLM 툴 루프.

발화 하나를 받아 응답 텍스트 조각을 스트리밍한다. 템플릿 응답은 한 조각이고,
LLM 응답은 토큰 단위로 흘러나온다.
"""

import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from app.config import Settings
from app.core.connections import ConnectionManager
from app.core.database import Database
from app.core.i18n import Translator
from app.core.openai_gateway import OpenAIGateway
from app.domain.agent.alarm_control_attempt import AlarmControlAttempt
from app.domain.agent.clarify import ClarifyStore
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import build_home_view, home_names
from app.domain.agent.memory import ConversationMemory, target_from_intent
from app.domain.agent.model import Clarify, Intent
from app.domain.agent.skills.alarm import AlarmSkill
from app.domain.agent.skills.alarm_control import AlarmControlSkill
from app.domain.agent.skills.alarm_select import AlarmSelector
from app.domain.agent.skills.alarm_store import AlarmStore
from app.domain.agent.skills.base import KEY_INTERNAL
from app.domain.agent.skills.broadcast import BroadcastSkill
from app.domain.agent.skills.clarify import ClarifySkill
from app.domain.agent.skills.device import DeviceSkill
from app.domain.agent.skills.registry import SkillRegistry
from app.domain.agent.skills.state import StateSkill
from app.domain.agent.skills.weather import WeatherSkill
from app.domain.agent.understand.context import ClarifyAnswerResolver, ContextAttempt
from app.domain.agent.understand.followup import FollowupResolver
from app.domain.agent.understand.llm import LlmStep
from app.domain.agent.understand.steps import MatcherAttempt, WeatherAttempt
from app.domain.agent.understand.understander import Understander
from app.domain.broadcast.service import BroadcastService
from app.domain.home.service import HomeService
from app.domain.telemetry.turn import Stage, Turn, TurnPath, TurnSource
from app.domain.weather.service import WeatherService


class AgentService:
    """발화 텍스트를 이해하고 실행해 응답 텍스트를 만든다. 앱 수명 동안 하나만 존재한다.

    확인 질문의 보류 상태와 직전 대화를 허브별로 메모리에 잡고 있으므로 인스턴스를 공유해야 한다.
    """

    def __init__(
        self,
        settings: Settings,
        translate: Translator,
        home: HomeService,
        connections: ConnectionManager,
        database: Callable[[], Database],
        openai: OpenAIGateway | None,
        clock: Callable[[], datetime] | None = None,
        weather: WeatherService | None = None,
        broadcast: BroadcastService | None = None,
    ) -> None:
        self._settings = settings
        self._t = translate
        self._home = home
        self._clock = clock
        self._tz = ZoneInfo(settings.timezone)
        self._monotonic: Callable[[], float] = time.monotonic
        clarify = ClarifyStore(settings.clarify_ttl_seconds, lambda: self._monotonic())
        self._memory = ConversationMemory(
            settings.followup_ttl_seconds,
            settings.history_ttl_seconds,
            lambda: self._monotonic(),
        )
        alarms = AlarmStore(database, connections)
        selector = AlarmSelector(alarms, self._tz)
        self._skills = SkillRegistry(
            [
                DeviceSkill(translate, home),
                StateSkill(translate),
                AlarmSkill(translate, alarms, selector, clarify, self._tz),
                AlarmControlSkill(translate, connections.hub_control),
                WeatherSkill(translate, weather),
                *([BroadcastSkill(translate, broadcast)] if broadcast is not None else []),
                ClarifySkill(translate, clarify),
            ],
            translate,
        )
        self._understander = Understander(
            [
                AlarmControlAttempt(connections.hub_control.ringing_alarm_id),
                ContextAttempt(
                    [
                        ClarifyAnswerResolver(clarify, alarms, selector),
                        FollowupResolver(self._memory),
                    ]
                ),
                MatcherAttempt(),
                WeatherAttempt(),
            ]
        )
        self._llm = LlmStep(settings, translate, openai, self._skills, self._memory)

    def now(self) -> datetime:
        """시간대가 있는 현재 시각."""
        return self._clock() if self._clock else datetime.now(self._tz)

    def stt_prompt(self) -> str | None:
        """STT 에 넘길 방·기기 이름 힌트. 설정이 꺼져 있거나 이름이 없으면 None."""
        if not self._settings.stt_prompt_enabled:
            return None
        names = home_names(build_home_view(self._home.snapshot()).model)
        if not names:
            return None
        return self._t("voice.stt_prompt").format(names=", ".join(names))

    async def handle(
        self, text: str, hub_id: str, area_id: str, turn: Turn | None = None
    ) -> AsyncIterator[str]:
        """발화를 처리하고 응답 텍스트 조각을 스트리밍한다.

        Args:
            text: STT 결과 또는 TextQuery 본문.
            hub_id: 말한 허브. 알람·타이머를 이 허브에 만든다. 모르면 빈 문자열.
            area_id: 말한 허브가 놓인 방. 모르면 빈 문자열.
            turn: 이 발화의 턴 기록. 경로·의도·단계 시간·응답을 채운다. 없으면 버려지는 기록을 쓴다.

        Yields:
            응답 텍스트 조각. 기기 제어 실패나 내부 오류도 사용자용 문장으로 낸다.
        """
        turn = turn or Turn(hub_id, TurnSource.TEXT, time.monotonic())
        view = build_home_view(self._home.snapshot())
        ctx = TurnContext(view, hub_id, area_id, self.now(), turn)
        reply = ""
        async for delta in self._run(text, ctx):
            turn.append_response(delta)
            reply += delta
            yield delta
        self._memory.add_turn(hub_id, text, reply.strip())

    async def _run(self, text: str, ctx: TurnContext) -> AsyncIterator[str]:
        turn = ctx.turn
        try:
            with turn.timed(Stage.UNDERSTAND):
                understanding = await self._understander.understand(text, ctx)
            intent = understanding.intent
            if intent is not None:
                turn.path = (
                    TurnPath.CLARIFY
                    if isinstance(intent, Clarify)
                    else TurnPath(understanding.step)
                )
                turn.intent = type(intent).__name__
                with turn.timed(Stage.ACT):
                    result = await self._skills.execute(intent, ctx)
                turn.record_skill(result.ok)
                self._remember_target(intent, result.ok, ctx)
                yield result.text
                return
        except Exception:
            logger.exception("Agent failed to execute command")
            turn.path = TurnPath.ERROR
            turn.fail("internal")
            yield self._t(KEY_INTERNAL)
            return
        turn.path = TurnPath.LLM
        async for delta in self._llm.stream(text, ctx):
            yield delta

    def _remember_target(self, intent: Intent, ok: bool, ctx: TurnContext) -> None:
        """성공한 기기·장면·상태 턴의 대상을 후속 지시용으로 기억한다."""
        if not ok:
            return
        target = target_from_intent(intent, ctx.view)
        if target is not None:
            self._memory.remember_target(ctx.hub_id, target)
