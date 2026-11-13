"""대화 에이전트: 결정론 매처 -> 실행 -> (실패 시) LLM 툴 루프.

발화 하나를 받아 응답 텍스트 조각을 스트리밍한다. 템플릿 응답은 한 조각이고,
LLM 응답은 토큰 단위로 흘러나온다.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from cozy.v1 import alarm_pb2
from cozy.v1.home_pb2 import EntityCommand
from loguru import logger

from app.config import Settings
from app.core.connections import ConnectionManager
from app.core.database import Database
from app.core.exceptions import CozyError
from app.core.i18n import Translator
from app.core.openai_gateway import OpenAIGateway, OpenAIGatewayError, ToolCall
from app.domain.agent import tools as tool_defs
from app.domain.agent.followup import (
    Answer,
    classify_answer,
    narrow_candidates,
    parse_ordinal,
)
from app.domain.agent.home_view import HomeView, build_home_view, home_names
from app.domain.agent.matcher import match
from app.domain.agent.model import (
    AlarmIntent,
    CancelAlarmIntent,
    Clarify,
    ClarifyReason,
    CommandParams,
    DeviceAction,
    DeviceCommand,
    EntityRef,
    MatchResult,
    NoMatch,
    SceneCommand,
    StateQuery,
    TimerIntent,
    UtteranceContext,
    WeatherIntent,
)
from app.domain.agent.normalize import compact_form, normalize
from app.domain.agent.responses import (
    build_response,
    build_state_response,
    render,
    which_alarm_response,
)
from app.domain.agent.schedule import match_cancel
from app.domain.alarm.schemas import AlarmRead
from app.domain.alarm.service import AlarmService
from app.domain.home.service import HomeService
from app.domain.hub.service import HubService
from app.domain.weather.schemas import WeatherWhen
from app.domain.weather.service import WeatherService

KEY_INTERNAL = "error.internal"
KEY_NOT_FOUND = "home.entity_not_found"
KEY_HUB_REQUIRED = "agent.hub_required"
MAX_LLM_ITERATIONS = 5
MIN_BRIGHTNESS_PCT = 1
MAX_BRIGHTNESS_PCT = 100
_PENDING_DELETE = "pending_delete"
_TIMER_KIND = "timer"
_MAX_LISTED_CANDIDATES = 5
_TIMER_REMAINING_TOLERANCE_SECONDS = 30


class _CancelStatus(StrEnum):
    DONE = "done"
    NONE = "none"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class _CancelOutcome:
    status: _CancelStatus
    error_key: str = ""
    rows: tuple[AlarmRead, ...] = ()


@dataclass(frozen=True, slots=True)
class _Ctx:
    """요청 하나의 문맥. 집 상태는 요청 시작 시점의 사본이다."""

    view: HomeView
    hub_id: str
    area_id: str
    now: datetime


@dataclass(slots=True)
class _Pending:
    clarify: Clarify
    expires_at: float


class AgentService:
    """발화 텍스트를 이해하고 실행해 응답 텍스트를 만든다. 앱 수명 동안 하나만 존재한다.

    확인 질문의 보류 상태를 허브별로 메모리에 잡고 있으므로 인스턴스를 공유해야 한다.
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
    ) -> None:
        self._settings = settings
        self._t = translate
        self._home = home
        self._connections = connections
        self._database = database
        self._openai = openai
        self._tz = ZoneInfo(settings.timezone)
        self._clock = clock
        self._weather = weather
        self._monotonic: Callable[[], float] = time.monotonic
        self._pending: dict[str, _Pending] = {}
        # 원래 맞춘 타이머 길이. 허브 스냅샷에는 만든 시각이 없어 서버가 기억한다.
        self._timer_seconds: dict[tuple[str, str], int] = {}

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

    async def handle(self, text: str, hub_id: str, area_id: str) -> AsyncIterator[str]:
        """발화를 처리하고 응답 텍스트 조각을 스트리밍한다.

        Args:
            text: STT 결과 또는 TextQuery 본문.
            hub_id: 말한 허브. 알람·타이머를 이 허브에 만든다. 모르면 빈 문자열.
            area_id: 말한 허브가 놓인 방. 모르면 빈 문자열.

        Yields:
            응답 텍스트 조각. 기기 제어 실패나 내부 오류도 사용자용 문장으로 낸다.
        """
        ctx = _Ctx(build_home_view(self._home.snapshot()), hub_id, area_id, self.now())
        try:
            reply = await self._follow_up(text, ctx)
            if reply is not None:
                yield reply
                return
            result = match(UtteranceContext(text, area_id or None, ctx.now), ctx.view.model)
            if not isinstance(result, NoMatch):
                yield await self._execute(result, ctx)
                return
        except Exception:
            logger.exception("Agent failed to execute command")
            yield self._t(KEY_INTERNAL)
            return
        async for delta in self._ask_llm(text, ctx):
            yield delta

    # --- 확인 질문 ------------------------------------------------------------------------

    def _remember(self, hub_id: str, clarify: Clarify) -> None:
        ttl = self._settings.clarify_ttl_seconds
        self._pending[hub_id] = _Pending(clarify, self._monotonic() + ttl)

    def _take_pending(self, hub_id: str) -> Clarify | None:
        pending = self._pending.pop(hub_id, None)
        if pending is None or pending.expires_at < self._monotonic():
            return None
        return pending.clarify

    async def _follow_up(self, text: str, ctx: _Ctx) -> str | None:
        """보류 중인 확인 질문에 대한 답이면 처리해 응답을 돌려준다. 아니면 None."""
        clarify = self._take_pending(ctx.hub_id)
        if clarify is None:
            return None
        answer = classify_answer(text)
        if answer is Answer.NO:
            return self._t("agent.clarify.cancelled")
        if clarify.reason is ClarifyReason.AMBIGUOUS_ALARM:
            return await self._pick_alarm(clarify, answer, text, ctx)
        if clarify.reason is ClarifyReason.BROAD_ACTION:
            return await self._confirm_broad(clarify, answer, ctx)
        return await self._pick_candidate(clarify, answer, text, ctx)

    async def _confirm_broad(
        self, clarify: Clarify, answer: Answer | None, ctx: _Ctx
    ) -> str | None:
        if answer not in (Answer.YES, Answer.ALL):
            return None
        if clarify.action is None:
            return await self._cancel_everything(ctx)
        command = DeviceCommand(
            clarify.candidate_entity_ids, clarify.action, clarify.params or CommandParams()
        )
        return await self._execute(command, ctx)

    async def _pick_candidate(
        self, clarify: Clarify, answer: Answer | None, text: str, ctx: _Ctx
    ) -> str | None:
        candidates = [e for i in clarify.candidate_entity_ids if (e := ctx.view.entity(i))]
        if answer is Answer.YES:
            # "응" 만으로는 고를 수 없다. 같은 질문을 다시 한다.
            return self._ask(clarify, ctx)
        if answer is Answer.ALL:
            chosen: tuple[EntityRef, ...] = tuple(candidates)
        else:
            narrowed = narrow_candidates(text, candidates, ctx.view.model)
            if not narrowed.entities or not narrowed.changed:
                return None
            chosen = narrowed.entities
        if len(chosen) > 1 and answer is not Answer.ALL:
            narrower = Clarify(
                reason=clarify.reason,
                candidate_entity_ids=tuple(e.entity_id for e in chosen),
                action=clarify.action,
                params=clarify.params,
            )
            return self._ask(narrower, ctx)
        return await self._execute(_resolved(clarify, chosen), ctx)

    def _ask(self, clarify: Clarify, ctx: _Ctx) -> str:
        self._remember(ctx.hub_id, clarify)
        response = build_response(clarify, ctx.view.model, ctx.now, self._t)
        return render(response, self._t) if response else self._t(KEY_INTERNAL)

    # --- 결정론 실행 ----------------------------------------------------------------------

    async def _execute(self, result: MatchResult, ctx: _Ctx) -> str:
        if isinstance(result, Clarify):
            return self._ask(result, ctx)
        if isinstance(result, DeviceCommand):
            return await self._run_device_command(result, ctx)
        if isinstance(result, SceneCommand):
            return await self._run_scene(result, ctx)
        if isinstance(result, StateQuery):
            return self._answer_query(result, ctx)
        if isinstance(result, AlarmIntent | TimerIntent):
            return await self._run_schedule(result, ctx)
        if isinstance(result, CancelAlarmIntent):
            return await self._run_cancel(result, ctx)
        if isinstance(result, WeatherIntent):
            return self._answer_weather(result.when)
        return self._t(KEY_INTERNAL)

    def _template(self, result: MatchResult, ctx: _Ctx) -> str:
        response = build_response(result, ctx.view.model, ctx.now, self._t)
        return render(response, self._t) if response else self._t(KEY_NOT_FOUND)

    async def _run_device_command(self, command: DeviceCommand, ctx: _Ctx) -> str:
        error_key = await self._apply_device(
            ctx.view,
            command.entity_ids,
            command.action.value,
            command.params.brightness_pct,
            command.params.brightness_delta_pct,
        )
        return self._t(error_key) if error_key else self._template(command, ctx)

    async def _run_scene(self, command: SceneCommand, ctx: _Ctx) -> str:
        error_key = await self._apply_device(ctx.view, (command.entity_id,), "activate", None, None)
        return self._t(error_key) if error_key else self._template(command, ctx)

    def _answer_query(self, query: StateQuery, ctx: _Ctx) -> str:
        answers: list[str] = []
        for entity_id in query.entity_ids:
            status = ctx.view.status.get(entity_id)
            response = status and build_state_response(ctx.view.model, entity_id, status.state)
            if response:
                answers.append(render(response, self._t))
        return ". ".join(answers) if answers else self._t(KEY_NOT_FOUND)

    async def _apply_device(
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

    # --- 알람·타이머 ----------------------------------------------------------------------

    @asynccontextmanager
    async def _alarm_service(self) -> AsyncIterator[AlarmService]:
        async with self._database().session() as session:
            hubs = HubService(session, self._connections)
            yield AlarmService(session, self._connections, hubs)

    async def _upsert_alarm(self, hub_id: str, alarm: alarm_pb2.Alarm) -> str | None:
        if not hub_id:
            return KEY_HUB_REQUIRED
        alarm.hub_id = hub_id
        try:
            async with self._alarm_service() as service:
                await service.handle_mobile_upsert(
                    alarm_pb2.AlarmUpsert(request_id=uuid.uuid4().hex, alarm=alarm)
                )
        except CozyError as exc:
            logger.warning("Alarm creation rejected: {}", exc)
            return exc.message_key
        return None

    def _new_alarm(
        self,
        kind: alarm_pb2.AlarmKind,
        label: str | None,
        *,
        fires_at: datetime | None = None,
        hour: int = 0,
        minute: int = 0,
        days: tuple[int, ...] = (),
    ) -> alarm_pb2.Alarm:
        alarm = alarm_pb2.Alarm(
            alarm_id=uuid.uuid4().hex, kind=kind, label=label or "", enabled=True
        )
        if fires_at is not None:
            alarm.one_shot.fires_at_epoch_ms = int(fires_at.timestamp() * 1000)
            return alarm
        alarm.repeating.hour = hour
        alarm.repeating.minute = minute
        alarm.repeating.days.extend(days)  # type: ignore[arg-type]
        return alarm

    async def _run_schedule(self, intent: AlarmIntent | TimerIntent, ctx: _Ctx) -> str:
        if isinstance(intent, TimerIntent):
            fires_at = ctx.now + timedelta(seconds=intent.duration_seconds)
            alarm = self._new_alarm(alarm_pb2.ALARM_KIND_TIMER, intent.label, fires_at=fires_at)
        elif intent.days is not None:
            alarm = self._new_alarm(
                alarm_pb2.ALARM_KIND_ALARM,
                intent.label,
                hour=intent.hour,
                minute=intent.minute,
                days=intent.days,
            )
        else:
            fires_at = datetime.fromtimestamp((intent.one_shot_epoch_ms or 0) / 1000, tz=self._tz)
            alarm = self._new_alarm(alarm_pb2.ALARM_KIND_ALARM, intent.label, fires_at=fires_at)
        error_key = await self._upsert_alarm(ctx.hub_id, alarm)
        if error_key:
            return self._t(error_key)
        if isinstance(intent, TimerIntent):
            self._timer_seconds[(ctx.hub_id, alarm.alarm_id)] = intent.duration_seconds
        return self._template(intent, ctx)

    async def _run_cancel(self, intent: CancelAlarmIntent, ctx: _Ctx) -> str:
        outcome = await self._cancel_matching(ctx.hub_id, intent, ctx.now)
        if outcome.status is _CancelStatus.ERROR:
            return self._t(outcome.error_key)
        if outcome.status is _CancelStatus.NONE:
            return self._t("agent.alarm.not_found")
        if outcome.status is _CancelStatus.AMBIGUOUS:
            return self._ask_which_alarm(outcome.rows, ctx)
        return self._template(intent, ctx)

    def _ask_which_alarm(self, rows: tuple[AlarmRead, ...], ctx: _Ctx) -> str:
        if len(rows) > _MAX_LISTED_CANDIDATES:
            return self._t("agent.alarm.which")
        ids = tuple(r.alarm_id for r in rows)
        self._remember(ctx.hub_id, Clarify(ClarifyReason.AMBIGUOUS_ALARM, candidate_alarm_ids=ids))
        return which_alarm_response(rows, ctx.now, self._t)

    async def _pick_alarm(
        self, clarify: Clarify, answer: Answer | None, text: str, ctx: _Ctx
    ) -> str | None:
        """어느 알람인지 되물은 질문에 대한 답("첫 번째", "7시 꺼")을 처리한다."""
        try:
            wanted = set(clarify.candidate_alarm_ids)
            rows = [r for r in await self._live_rows(ctx.hub_id) if r.alarm_id in wanted]
            if not rows:
                return self._t("agent.alarm.not_found")
            chosen = self._choose_alarm(rows, answer, text, ctx)
            if chosen is None:
                return None
            if len(chosen) > 1 and answer is not Answer.ALL:
                return self._ask_which_alarm(tuple(chosen), ctx)
            await self._delete_rows(ctx.hub_id, chosen)
        except CozyError as exc:
            logger.warning("Alarm cancel rejected: {}", exc)
            return self._t(exc.message_key)
        if len(chosen) > 1:
            return self._t("agent.alarm.cancel_all")
        key = "agent.timer.cancel" if chosen[0].kind == _TIMER_KIND else "agent.alarm.cancel"
        return self._t(key)

    def _choose_alarm(
        self, rows: list[AlarmRead], answer: Answer | None, text: str, ctx: _Ctx
    ) -> list[AlarmRead] | None:
        """답으로 후보를 고른다. 가르지 못하면 None(일반 발화로 다시 처리한다)."""
        if answer is Answer.ALL:
            return rows
        if answer is Answer.YES:
            return None
        ordinal = parse_ordinal(text)
        if ordinal is not None:
            return [rows[ordinal - 1]] if ordinal <= len(rows) else None
        noun = "타이머" if all(r.kind == _TIMER_KIND for r in rows) else "알람"
        result = match_cancel(normalize(f"{text} {noun}"), ctx.now)
        if not isinstance(result, CancelAlarmIntent):
            return None
        return [r for r in rows if self._matches(r, result, ctx.now)] or None

    async def _cancel_everything(self, ctx: _Ctx) -> str:
        outcome = await self._cancel_matching(
            ctx.hub_id, CancelAlarmIntent(), ctx.now, everything=True
        )
        if outcome.status is _CancelStatus.ERROR:
            return self._t(outcome.error_key)
        if outcome.status is _CancelStatus.NONE:
            return self._t("agent.alarm.not_found")
        return self._t("agent.alarm.cancel_all")

    def _local_time(self, row: AlarmRead) -> tuple[int, int] | None:
        if row.hour is not None and row.minute is not None:
            return row.hour, row.minute
        if row.fires_at_epoch_ms is None:
            return None
        fires = datetime.fromtimestamp(row.fires_at_epoch_ms / 1000, tz=self._tz)
        return fires.hour, fires.minute

    def _timer_matches(self, row: AlarmRead, seconds: int, now: datetime) -> bool:
        """타이머가 원래 `seconds` 길이였는지 본다.

        서버가 맞춘 타이머는 기억해 둔 원래 길이로 정확히 비교한다. 앱이나 서버 재시작 전에
        만든 타이머처럼 기억이 없으면 남은 시간이 말한 길이에 가까운(±30초) 것만 인정한다.
        """
        original = self._timer_seconds.get((row.hub_id, row.alarm_id))
        if original is not None:
            return original == seconds
        if row.fires_at_epoch_ms is None:
            return False
        remaining = row.fires_at_epoch_ms / 1000 - now.timestamp()
        return abs(remaining - seconds) <= _TIMER_REMAINING_TOLERANCE_SECONDS

    def _day_matches(self, row: AlarmRead, day_offset: int, now: datetime) -> bool:
        """일회성 알람이 day_offset 일 뒤에 울리는지 본다. 반복 알람은 날짜로 지목하지 않는다."""
        if row.fires_at_epoch_ms is None:
            return False
        fires = datetime.fromtimestamp(row.fires_at_epoch_ms / 1000, tz=self._tz)
        return (fires.date() - now.astimezone(self._tz).date()).days == day_offset

    def _matches(self, row: AlarmRead, intent: CancelAlarmIntent, now: datetime) -> bool:
        if intent.label:
            wanted, have = compact_form(intent.label), compact_form(row.label)
            if wanted not in have and have not in wanted:
                return False
        if intent.duration_seconds is not None and not self._timer_matches(
            row, intent.duration_seconds, now
        ):
            return False
        if intent.day_offset is not None and not self._day_matches(row, intent.day_offset, now):
            return False
        if intent.hour is None or intent.minute is None:
            return True
        clock = self._local_time(row)
        if clock is None or clock[1] != intent.minute:
            return False
        if intent.hour_ambiguous:
            return clock[0] % 12 == intent.hour % 12
        return clock[0] == intent.hour

    async def _live_rows(self, hub_id: str) -> list[AlarmRead]:
        async with self._alarm_service() as service:
            rows = await service.list_alarms(hub_id)
        return [r for r in rows if r.sync_state != _PENDING_DELETE]

    async def _delete_rows(self, hub_id: str, rows: list[AlarmRead]) -> None:
        """AlarmDelete 를 허브로 보낸다. 거부되면 CozyError 를 그대로 올린다."""
        async with self._alarm_service() as service:
            for row in rows:
                await service.handle_mobile_delete(
                    alarm_pb2.AlarmDelete(
                        request_id=uuid.uuid4().hex, alarm_id=row.alarm_id, hub_id=hub_id
                    )
                )
                self._timer_seconds.pop((hub_id, row.alarm_id), None)

    async def _cancel_matching(
        self, hub_id: str, intent: CancelAlarmIntent, now: datetime, *, everything: bool = False
    ) -> _CancelOutcome:
        """조건에 맞는 알람·타이머를 허브에서 지운다. 대상이 둘 이상이면 지우지 않고 알린다."""
        if not hub_id:
            return _CancelOutcome(_CancelStatus.ERROR, KEY_HUB_REQUIRED)
        try:
            rows = [
                r
                for r in await self._live_rows(hub_id)
                if everything or (r.kind == _TIMER_KIND) == intent.is_timer
            ]
            targets = rows if everything else [r for r in rows if self._matches(r, intent, now)]
            if not targets:
                return _CancelOutcome(_CancelStatus.NONE)
            if len(targets) > 1 and not everything:
                return _CancelOutcome(_CancelStatus.AMBIGUOUS, rows=tuple(targets))
            await self._delete_rows(hub_id, targets)
        except CozyError as exc:
            logger.warning("Alarm cancel rejected: {}", exc)
            return _CancelOutcome(_CancelStatus.ERROR, exc.message_key)
        return _CancelOutcome(_CancelStatus.DONE)

    # --- LLM 폴백 -------------------------------------------------------------------------

    def _system_prompt(self, ctx: _Ctx) -> str:
        weekday = self._t(f"agent.day.{ctx.now.isoweekday()}")
        context = self._t("agent.llm.context").format(
            area=ctx.view.area_name(ctx.area_id) or self._t("agent.llm.area_unknown"),
            now=f"{ctx.now:%Y-%m-%d %H:%M} {weekday}",
        )
        return f"{self._t('agent.llm.system')}\n{context}"

    async def _ask_llm(self, text: str, ctx: _Ctx) -> AsyncIterator[str]:
        if self._openai is None:
            yield self._t("voice.unavailable")
            return
        tools = tool_defs.build_tools(ctx.view)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(ctx)},
            {"role": "user", "content": text},
        ]
        emitted = False
        try:
            for turn in range(MAX_LLM_ITERATIONS):
                # 마지막 턴은 툴 없이 불러 반드시 텍스트로 끝나게 한다.
                is_last = turn == MAX_LLM_ITERATIONS - 1
                content = ""
                calls: tuple[ToolCall, ...] = ()
                stream = self._openai.stream_chat(messages, None if is_last else tools)
                async for chunk in self._with_idle_timeout(stream):
                    if chunk.text:
                        emitted = True
                        content += chunk.text
                        yield chunk.text
                    calls = chunk.tool_calls or calls
                if not calls:
                    return
                messages.append(_assistant_message(content, calls))
                for call in calls:
                    result = await self._run_tool(call, ctx)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        except (TimeoutError, OpenAIGatewayError) as exc:
            logger.warning("LLM fallback failed: {}", exc or type(exc).__name__)
            if not emitted:
                yield self._t("agent.llm.apology")

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

    async def _run_tool(self, call: ToolCall, ctx: _Ctx) -> str:
        args = call.parsed_arguments()
        handlers = {
            tool_defs.TOOL_CONTROL: self._tool_control,
            tool_defs.TOOL_STATE: self._tool_state,
            tool_defs.TOOL_SET_ALARM: self._tool_set_alarm,
            tool_defs.TOOL_SET_TIMER: self._tool_set_timer,
            tool_defs.TOOL_CANCEL: self._tool_cancel,
            tool_defs.TOOL_WEATHER: self._tool_weather,
        }
        handler = handlers.get(call.name)
        if handler is None:
            payload: dict[str, Any] = {"ok": False, "error": "unknown_tool"}
        else:
            try:
                payload = await handler(args, ctx)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Invalid tool arguments: tool={} error={}", call.name, exc)
                payload = {"ok": False, "error": "invalid_arguments"}
        logger.debug("Tool executed: {} ok={}", call.name, payload.get("ok"))
        return json.dumps(payload, ensure_ascii=False)

    def _answer_weather(self, when: WeatherWhen) -> str:
        if self._weather is None:
            return self._t("weather.disabled")
        return self._weather.answer(when, self._t)

    async def _tool_weather(self, args: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
        if self._weather is None:
            return {"ok": False, "error": "disabled"}
        return self._weather.tool_payload(WeatherWhen(str(args["when"])), self._t)

    async def _tool_control(self, args: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
        entity_id = str(args["entity_id"])
        action = str(args["action"])
        entity = ctx.view.entity(entity_id)
        if entity is None or action not in tool_defs.CONTROL_ACTIONS:
            raise ValueError("unknown entity or action")
        pct = _optional_int(args, "brightness_pct", 0, 100)
        delta = _optional_int(args, "brightness_delta_pct", -100, 100)
        if action == DeviceAction.SET_BRIGHTNESS and pct is None and delta is None:
            raise ValueError("brightness required")
        error_key = await self._apply_device(ctx.view, (entity_id,), action, pct, delta)
        if error_key:
            return {"ok": False, "error": self._t(error_key)}
        return {"ok": True}

    async def _tool_state(self, args: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
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
        return payload

    async def _tool_set_alarm(self, args: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
        hour = _required_int(args, "hour", 0, 23)
        minute = _required_int(args, "minute", 0, 59)
        label = str(args.get("label") or "")
        raw_days = args.get("days") or []
        days = tuple(sorted({_bounded(int(d), 1, 7) for d in raw_days}))
        if days:
            alarm = self._new_alarm(
                alarm_pb2.ALARM_KIND_ALARM, label, hour=hour, minute=minute, days=days
            )
            summary: dict[str, Any] = {"days": list(days), "time": f"{hour:02d}:{minute:02d}"}
        else:
            fires_at = self._one_shot_time(args.get("date"), hour, minute, ctx.now)
            alarm = self._new_alarm(alarm_pb2.ALARM_KIND_ALARM, label, fires_at=fires_at)
            summary = {"fires_at": fires_at.isoformat(timespec="minutes")}
        error_key = await self._upsert_alarm(ctx.hub_id, alarm)
        if error_key:
            return {"ok": False, "error": self._t(error_key)}
        return {"ok": True, **summary}

    def _one_shot_time(self, raw_date: object, hour: int, minute: int, now: datetime) -> datetime:
        if raw_date:
            target = datetime.combine(
                date.fromisoformat(str(raw_date)), dtime(hour, minute), self._tz
            )
            if target <= now:
                raise ValueError("date is in the past")
            return target
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target if target > now else target + timedelta(days=1)

    async def _tool_set_timer(self, args: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
        seconds = _required_int(args, "duration_seconds", 1, tool_defs.MAX_TIMER_SECONDS)
        fires_at = ctx.now + timedelta(seconds=seconds)
        alarm = self._new_alarm(
            alarm_pb2.ALARM_KIND_TIMER, str(args.get("label") or ""), fires_at=fires_at
        )
        error_key = await self._upsert_alarm(ctx.hub_id, alarm)
        if error_key:
            return {"ok": False, "error": self._t(error_key)}
        self._timer_seconds[(ctx.hub_id, alarm.alarm_id)] = seconds
        return {"ok": True, "duration_seconds": seconds}

    async def _tool_cancel(self, args: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
        hour = _optional_int(args, "hour", 0, 23)
        minute = _optional_int(args, "minute", 0, 59)
        intent = CancelAlarmIntent(
            label=str(args["label"]) if args.get("label") else None,
            hour=hour,
            minute=(minute or 0) if hour is not None else None,
            is_timer=bool(args.get("is_timer")),
        )
        outcome = await self._cancel_matching(ctx.hub_id, intent, ctx.now)
        if outcome.status is _CancelStatus.ERROR:
            return {"ok": False, "error": self._t(outcome.error_key)}
        if outcome.status is _CancelStatus.DONE:
            return {"ok": True}
        return {"ok": False, "error": outcome.status.value}


def _ha_action(domain: str, action: str) -> str:
    if action == "activate" and domain == "script":
        return "run"
    return action


def _bounded(value: int, low: int, high: int) -> int:
    if not low <= value <= high:
        raise ValueError(f"{value} not in {low}..{high}")
    return value


def _required_int(args: dict[str, Any], key: str, low: int, high: int) -> int:
    return _bounded(int(args[key]), low, high)


def _optional_int(args: dict[str, Any], key: str, low: int, high: int) -> int | None:
    value = args.get(key)
    return None if value is None else _bounded(int(value), low, high)


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


def _resolved(clarify: Clarify, chosen: tuple[EntityRef, ...]) -> MatchResult:
    """확인이 끝난 후보로 원래 의도했던 명령을 만든다."""
    ids = tuple(e.entity_id for e in chosen)
    if clarify.action is not None:
        return DeviceCommand(ids, clarify.action, clarify.params or CommandParams())
    if chosen[0].domain == "scene":
        return SceneCommand(ids[0])
    return StateQuery(ids)


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
