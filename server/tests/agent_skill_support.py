"""스킬·Understander 단위 테스트 공용 도구: 호출을 기록하는 가짜 HomeService·AlarmStore."""

from zoneinfo import ZoneInfo

from cozy.v1 import alarm_pb2
from cozy.v1.common_pb2 import ERROR_CODE_UPSTREAM_UNAVAILABLE
from cozy.v1.home_pb2 import CommandResult, EntityCommand

from app.core.i18n import Translator
from app.domain.agent.context import TurnContext
from app.domain.agent.home_view import EntityStatus, HomeView
from app.domain.agent.skills.alarm_store import PENDING_DELETE, AlarmStore, UpsertOutcome
from app.domain.alarm.schemas import AlarmRead
from app.domain.telemetry.turn import Turn, TurnSource
from tests.agent_fixtures import BEDROOM, HOME, LIVING, NOW

TZ = ZoneInfo("Asia/Seoul")
translate = Translator("ko")
HUB = "hub-1"


class FakeHome:
    """HomeService 대역. 받은 EntityCommand 를 기록하고 fail_key 가 있으면 실패로 답한다."""

    def __init__(self) -> None:
        self.commands: list[EntityCommand] = []
        self.fail_key = ""
        self.forbidden: set[str] = set()

    def entity_controllable(self, entity_id: str, *, voice: bool) -> bool:
        return entity_id not in self.forbidden

    async def execute(self, command: EntityCommand, *, voice: bool = False) -> CommandResult:
        self.commands.append(command)
        if self.fail_key:
            return CommandResult(
                request_id=command.request_id,
                ok=False,
                code=ERROR_CODE_UPSTREAM_UNAVAILABLE,
                message_key=self.fail_key,
            )
        return CommandResult(request_id=command.request_id, ok=True)

    def calls(self) -> list[tuple[str, str, dict[str, str]]]:
        """(entity_id, action, params) 목록. request_id 는 매번 달라 비교에서 뺀다."""
        return [(c.entity_id, c.action, dict(c.params)) for c in self.commands]


class FakeAlarmStore(AlarmStore):
    """AlarmStore 대역. DB 대신 메모리 목록을 쓰고 허브로 간 알람·삭제를 기록한다."""

    def __init__(self) -> None:
        super().__init__(lambda: None, None)  # type: ignore[arg-type]
        self.created: list[alarm_pb2.Alarm] = []
        self.deleted: list[str] = []
        self.rows: list[AlarmRead] = []
        self.reject_key = ""
        self.pending = False

    async def upsert(self, hub_id: str, alarm: alarm_pb2.Alarm) -> UpsertOutcome:
        if not hub_id:
            return UpsertOutcome("agent.hub_required")
        if self.reject_key:
            return UpsertOutcome(self.reject_key)
        alarm.hub_id = hub_id
        self.created.append(alarm)
        return UpsertOutcome(pending=self.pending)

    async def live_rows(self, hub_id: str) -> list[AlarmRead]:
        return [r for r in self.rows if r.hub_id == hub_id and r.sync_state != PENDING_DELETE]

    async def delete_rows(self, hub_id: str, rows: list[AlarmRead]) -> None:
        self.deleted.extend(r.alarm_id for r in rows)
        self.rows = [r for r in self.rows if r.alarm_id not in {x.alarm_id for x in rows}]


def alarm_row(
    alarm_id: str,
    *,
    kind: str = "alarm",
    hour: int | None = 7,
    minute: int | None = 0,
    label: str = "",
    fires_at_epoch_ms: int | None = None,
    hub_id: str = HUB,
) -> AlarmRead:
    return AlarmRead(
        hub_id=hub_id,
        alarm_id=alarm_id,
        kind=kind,
        label=label,
        fires_at_epoch_ms=fires_at_epoch_ms,
        hour=hour,
        minute=minute,
        days=[],
        enabled=True,
        sunrise=False,
        revision=1,
        sync_state="synced",
        updated_at=NOW,
    )


def make_ctx(
    hub_id: str = HUB, area_id: str = LIVING, states: dict[str, EntityStatus] | None = None
) -> TurnContext:
    status = states or {
        "light.living_ceiling": EntityStatus("on", {"brightness": "128"}),
        "light.bedroom_ceiling": EntityStatus("off"),
        "sensor.living_temp": EntityStatus("21.5", {"unit_of_measurement": "C"}),
    }
    return TurnContext(
        HomeView(HOME, status), hub_id, area_id, NOW, Turn(hub_id, TurnSource.TEXT, 0.0)
    )


__all__ = ["BEDROOM", "HUB", "NOW", "TZ", "FakeAlarmStore", "FakeHome", "alarm_row", "make_ctx"]
