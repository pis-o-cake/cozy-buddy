"""알람·타이머 스킬: 설정, 취소, 되묻기 후보 처리."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from cozy.v1 import alarm_pb2
from loguru import logger

from app.core.exceptions import CozyError
from app.core.i18n import Translator
from app.domain.agent.clarify import ClarifyStore
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import HomeView
from app.domain.agent.model import (
    AlarmIntent,
    CancelAlarmIntent,
    CancelAllAlarms,
    CancelSelected,
    Clarify,
    ClarifyReason,
    Intent,
    ReminderIntent,
    TimerIntent,
)
from app.domain.agent.responses import which_alarm_response
from app.domain.agent.skills.alarm_select import AlarmSelector
from app.domain.agent.skills.alarm_store import (
    KEY_HUB_REQUIRED,
    REMINDER_KIND,
    TIMER_KIND,
    AlarmStore,
    new_alarm,
)
from app.domain.agent.skills.alarm_tools import (
    MAX_TIMER_SECONDS,
    TOOL_CANCEL,
    TOOL_SET_ALARM,
    TOOL_SET_REMINDER,
    TOOL_SET_TIMER,
    alarm_tool_specs,
)
from app.domain.agent.skills.base import (
    KEY_INTERNAL,
    BaseSkill,
    SkillResult,
    bounded,
    optional_int,
    required_int,
)
from app.domain.alarm.schemas import AlarmRead

KEY_NOT_FOUND = "agent.alarm.not_found"
KEY_CANCEL_ALL = "agent.alarm.cancel_all"
KEY_WHICH = "agent.alarm.which"
_MAX_LISTED_CANDIDATES = 5


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


class AlarmSkill(BaseSkill):
    """알람 설정·타이머·취소. 결정론 의도와 LLM 툴이 같은 `_schedule`·`_cancel_matching` 을 쓴다."""

    intents = (
        AlarmIntent,
        TimerIntent,
        ReminderIntent,
        CancelAlarmIntent,
        CancelSelected,
        CancelAllAlarms,
    )
    tool_names = frozenset({TOOL_SET_ALARM, TOOL_SET_TIMER, TOOL_SET_REMINDER, TOOL_CANCEL})

    def __init__(
        self,
        translate: Translator,
        store: AlarmStore,
        selector: AlarmSelector,
        clarify: ClarifyStore,
        tz: ZoneInfo,
    ) -> None:
        super().__init__(translate)
        self._store = store
        self._selector = selector
        self._clarify = clarify
        self._tz = tz

    def tool_specs(self, view: HomeView) -> list[dict[str, Any]]:
        return alarm_tool_specs()

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        if isinstance(intent, AlarmIntent | TimerIntent | ReminderIntent):
            error_key = await self._schedule(intent, ctx)
            return self.failed(error_key) if error_key else self.templated(intent, ctx)
        if isinstance(intent, CancelAlarmIntent):
            outcome = await self._cancel_matching(ctx, intent)
            return self._cancel_result(outcome, intent, ctx)
        if isinstance(intent, CancelSelected):
            return await self._cancel_selected(intent, ctx)
        if isinstance(intent, CancelAllAlarms):
            return await self._cancel_all(ctx)
        return self.failed(KEY_INTERNAL)

    async def run_tool(self, name: str, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        if name == TOOL_SET_ALARM:
            return await self._tool_set_alarm(args, ctx)
        if name == TOOL_SET_TIMER:
            return await self._tool_set_timer(args, ctx)
        if name == TOOL_SET_REMINDER:
            return await self._tool_set_reminder(args, ctx)
        return await self._tool_cancel(args, ctx)

    # --- 설정 -----------------------------------------------------------------------------

    async def _schedule(
        self, intent: AlarmIntent | TimerIntent | ReminderIntent, ctx: TurnContext
    ) -> str | None:
        """알람·타이머를 허브에 만든다. 실패하면 사용자 메시지 키를, 성공하면 None 을 돌려준다."""
        kind = (
            alarm_pb2.ALARM_KIND_REMINDER
            if isinstance(intent, ReminderIntent)
            else alarm_pb2.ALARM_KIND_ALARM
        )
        if isinstance(intent, TimerIntent):
            fires_at = ctx.now + timedelta(seconds=intent.duration_seconds)
            alarm = new_alarm(alarm_pb2.ALARM_KIND_TIMER, intent.label, fires_at=fires_at)
        elif intent.days is not None:
            alarm = new_alarm(
                kind,
                intent.label,
                hour=intent.hour,
                minute=intent.minute,
                days=intent.days,
            )
        else:
            fires_at = datetime.fromtimestamp((intent.one_shot_epoch_ms or 0) / 1000, tz=self._tz)
            alarm = new_alarm(kind, intent.label, fires_at=fires_at)
        error_key = await self._store.upsert(ctx.hub_id, alarm)
        if error_key:
            return error_key
        if isinstance(intent, TimerIntent):
            self._store.remember_timer(ctx.hub_id, alarm.alarm_id, intent.duration_seconds)
        return None

    async def _tool_set_alarm(self, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        hour = required_int(args, "hour", 0, 23)
        minute = required_int(args, "minute", 0, 59)
        label = str(args.get("label") or "") or None
        days = tuple(sorted({bounded(int(d), 1, 7) for d in args.get("days") or []}))
        if days:
            intent = AlarmIntent(hour, minute, days=days, label=label)
            summary: dict[str, Any] = {"days": list(days), "time": f"{hour:02d}:{minute:02d}"}
        else:
            fires_at = self._one_shot_time(args.get("date"), hour, minute, ctx.now)
            epoch_ms = int(fires_at.timestamp() * 1000)
            intent = AlarmIntent(hour, minute, one_shot_epoch_ms=epoch_ms, label=label)
            summary = {"fires_at": fires_at.isoformat(timespec="minutes")}
        error_key = await self._schedule(intent, ctx)
        if error_key:
            return self.failed(error_key)
        return SkillResult(True, payload={"ok": True, **summary})

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

    async def _tool_set_timer(self, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        seconds = required_int(args, "duration_seconds", 1, MAX_TIMER_SECONDS)
        label = str(args.get("label") or "") or None
        error_key = await self._schedule(TimerIntent(seconds, label), ctx)
        if error_key:
            return self.failed(error_key)
        return SkillResult(True, payload={"ok": True, "duration_seconds": seconds})

    async def _tool_set_reminder(self, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        message = str(args["message"]).strip()
        if not message:
            raise ValueError("message is empty")
        if args.get("delay_seconds") is not None:
            seconds = required_int(args, "delay_seconds", 1, MAX_TIMER_SECONDS)
            fires_at = ctx.now + timedelta(seconds=seconds)
            intent = ReminderIntent(
                message,
                fires_at.hour,
                fires_at.minute,
                one_shot_epoch_ms=int(fires_at.timestamp() * 1000),
            )
            summary: dict[str, Any] = {"fires_at": fires_at.isoformat(timespec="minutes")}
        else:
            hour = required_int(args, "hour", 0, 23)
            minute = required_int(args, "minute", 0, 59)
            days = tuple(sorted({bounded(int(d), 1, 7) for d in args.get("days") or []}))
            if days:
                intent = ReminderIntent(message, hour, minute, days=days)
                summary = {"days": list(days), "time": f"{hour:02d}:{minute:02d}"}
            else:
                fires_at = self._one_shot_time(args.get("date"), hour, minute, ctx.now)
                epoch_ms = int(fires_at.timestamp() * 1000)
                intent = ReminderIntent(message, hour, minute, one_shot_epoch_ms=epoch_ms)
                summary = {"fires_at": fires_at.isoformat(timespec="minutes")}
        error_key = await self._schedule(intent, ctx)
        if error_key:
            return self.failed(error_key)
        return SkillResult(True, payload={"ok": True, "message": message, **summary})

    # --- 취소 -----------------------------------------------------------------------------

    async def _tool_cancel(self, args: dict[str, Any], ctx: TurnContext) -> SkillResult:
        hour = optional_int(args, "hour", 0, 23)
        minute = optional_int(args, "minute", 0, 59)
        intent = CancelAlarmIntent(
            label=str(args["label"]) if args.get("label") else None,
            hour=hour,
            minute=(minute or 0) if hour is not None else None,
            is_timer=bool(args.get("is_timer")),
        )
        outcome = await self._cancel_matching(ctx, intent)
        if outcome.status is _CancelStatus.ERROR:
            return self.failed(outcome.error_key)
        if outcome.status is _CancelStatus.DONE:
            return SkillResult(True, payload={"ok": True})
        return SkillResult(False, payload={"ok": False, "error": outcome.status.value})

    def _cancel_result(
        self, outcome: _CancelOutcome, intent: CancelAlarmIntent, ctx: TurnContext
    ) -> SkillResult:
        if outcome.status is _CancelStatus.ERROR:
            return self.failed(outcome.error_key)
        if outcome.status is _CancelStatus.NONE:
            return self.failed(KEY_NOT_FOUND)
        if outcome.status is _CancelStatus.AMBIGUOUS:
            return self._ask_which(outcome.rows, ctx)
        return self.templated(intent, ctx)

    async def _cancel_matching(
        self, ctx: TurnContext, intent: CancelAlarmIntent, *, everything: bool = False
    ) -> _CancelOutcome:
        """조건에 맞는 알람·타이머를 허브에서 지운다. 대상이 둘 이상이면 지우지 않고 알린다."""
        if not ctx.hub_id:
            return _CancelOutcome(_CancelStatus.ERROR, KEY_HUB_REQUIRED)
        try:
            rows = [
                r
                for r in await self._store.live_rows(ctx.hub_id)
                if r.kind != REMINDER_KIND
                and (everything or (r.kind == TIMER_KIND) == intent.is_timer)
            ]
            targets = (
                rows
                if everything
                else [r for r in rows if self._selector.matches(r, intent, ctx.now)]
            )
            if not targets:
                return _CancelOutcome(_CancelStatus.NONE)
            if len(targets) > 1 and not everything:
                return _CancelOutcome(_CancelStatus.AMBIGUOUS, rows=tuple(targets))
            await self._store.delete_rows(ctx.hub_id, targets)
        except CozyError as exc:
            logger.warning("Alarm cancel rejected: {}", exc)
            return _CancelOutcome(_CancelStatus.ERROR, exc.message_key)
        return _CancelOutcome(_CancelStatus.DONE)

    async def _cancel_all(self, ctx: TurnContext) -> SkillResult:
        outcome = await self._cancel_matching(ctx, CancelAlarmIntent(), everything=True)
        if outcome.status is _CancelStatus.ERROR:
            return self.failed(outcome.error_key)
        if outcome.status is _CancelStatus.NONE:
            return self.failed(KEY_NOT_FOUND)
        return SkillResult(True, KEY_CANCEL_ALL, text=self._t(KEY_CANCEL_ALL))

    async def _cancel_selected(self, intent: CancelSelected, ctx: TurnContext) -> SkillResult:
        """되물은 질문에 답해 고른 알람을 지운다."""
        try:
            wanted = set(intent.alarm_ids)
            rows = [r for r in await self._store.live_rows(ctx.hub_id) if r.alarm_id in wanted]
            if not rows:
                return self.failed(KEY_NOT_FOUND)
            if len(rows) > 1 and not intent.confirmed:
                return self._ask_which(tuple(rows), ctx)
            await self._store.delete_rows(ctx.hub_id, rows)
        except CozyError as exc:
            logger.warning("Alarm cancel rejected: {}", exc)
            return self.failed(exc.message_key)
        if len(rows) > 1:
            return SkillResult(True, KEY_CANCEL_ALL, text=self._t(KEY_CANCEL_ALL))
        key = "agent.timer.cancel" if rows[0].kind == TIMER_KIND else "agent.alarm.cancel"
        return SkillResult(True, key, text=self._t(key))

    def _ask_which(self, rows: tuple[AlarmRead, ...], ctx: TurnContext) -> SkillResult:
        if len(rows) > _MAX_LISTED_CANDIDATES:
            return SkillResult(True, KEY_WHICH, text=self._t(KEY_WHICH))
        ids = tuple(r.alarm_id for r in rows)
        self._clarify.remember(
            ctx.hub_id, Clarify(ClarifyReason.AMBIGUOUS_ALARM, candidate_alarm_ids=ids)
        )
        text = which_alarm_response(rows, ctx.now, self._t)
        return SkillResult(True, "agent.alarm.which_options", text=text)
