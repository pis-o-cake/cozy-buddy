"""발화로 지목한 알람·타이머를 고른다."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.agent.followup import Answer, parse_ordinal
from app.domain.agent.model import CancelAlarmIntent
from app.domain.agent.normalize import compact_form, normalize
from app.domain.agent.schedule import match_cancel
from app.domain.agent.skills.alarm_store import TIMER_KIND, AlarmStore
from app.domain.alarm.schemas import AlarmRead

_TIMER_REMAINING_TOLERANCE_SECONDS = 30


class AlarmSelector:
    """CancelAlarmIntent 조건에 맞는 행을 가려낸다."""

    def __init__(self, store: AlarmStore, tz: ZoneInfo) -> None:
        self._store = store
        self._tz = tz

    def matches(self, row: AlarmRead, intent: CancelAlarmIntent, now: datetime) -> bool:
        """행이 의도의 라벨·길이·날짜·시각 조건을 모두 만족하는지."""
        if intent.label:
            wanted, have = compact_form(intent.label), compact_form(row.label)
            # IMPORTANT: 빈 문자열은 모든 문자열의 부분 문자열이라 라벨 없는 행이 걸러지지 않는다.
            if not have or not wanted or (wanted not in have and have not in wanted):
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

    def choose(
        self, rows: list[AlarmRead], answer: Answer | None, text: str, now: datetime
    ) -> list[AlarmRead] | None:
        """확인 질문의 답으로 후보를 고른다. 가르지 못하면 None(일반 발화로 다시 처리한다)."""
        if answer is Answer.ALL:
            return rows
        if answer is Answer.YES:
            return None
        ordinal = parse_ordinal(text)
        if ordinal is not None:
            return [rows[ordinal - 1]] if ordinal <= len(rows) else None
        noun = "타이머" if all(r.kind == TIMER_KIND for r in rows) else "알람"
        result = match_cancel(normalize(f"{text} {noun}"), now)
        if not isinstance(result, CancelAlarmIntent):
            return None
        return [r for r in rows if self.matches(r, result, now)] or None

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
        original = self._store.original_timer_seconds(row.hub_id, row.alarm_id)
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
