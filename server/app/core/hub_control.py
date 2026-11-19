"""허브 장치 상태 사본과 허브에 보낸 제어 요청의 응답 대기."""

import asyncio
import uuid
from typing import TYPE_CHECKING

from cozy.v1.alarm_pb2 import AlarmControl, AlarmControlAction, AlarmControlResult
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import HubStatus
from loguru import logger

if TYPE_CHECKING:
    from app.core.connections import ConnectionManager

DEFAULT_CONTROL_TIMEOUT_SECONDS = 3.0


class HubControl:
    """허브가 알린 울림 상태를 기억하고 AlarmControl 결과를 기다린다. 메모리에만 있다."""

    def __init__(
        self,
        connections: "ConnectionManager",
        timeout_seconds: float = DEFAULT_CONTROL_TIMEOUT_SECONDS,
    ) -> None:
        self._connections = connections
        self._timeout = timeout_seconds
        self._ringing: dict[str, str] = {}
        self._pending: dict[str, tuple[str, asyncio.Future[AlarmControlResult]]] = {}

    def update_status(self, hub_id: str, status: HubStatus) -> None:
        """허브가 보낸 HubStatus 를 반영한다. 울리지 않으면 빈 문자열이 온다."""
        if status.ringing_alarm_id:
            self._ringing[hub_id] = status.ringing_alarm_id
        else:
            self._ringing.pop(hub_id, None)
        logger.debug("Hub status: hub={} ringing={}", hub_id, status.ringing_alarm_id or "-")

    def ringing_alarm_id(self, hub_id: str) -> str:
        """허브가 지금 울리고 있다고 알린 알람 ID. 울리지 않거나 모르면 빈 문자열."""
        return self._ringing.get(hub_id, "")

    def forget(self, hub_id: str) -> None:
        """허브 연결이 끊기거나 바뀌었을 때 상태 사본을 버린다."""
        self._ringing.pop(hub_id, None)

    def resolve(self, hub_id: str, result: AlarmControlResult) -> None:
        """허브의 AlarmControlResult 를 기다리는 요청에 전달한다. 모르는 요청은 무시한다."""
        entry = self._pending.get(result.request_id)
        if entry is None or entry[0] != hub_id or entry[1].done():
            logger.debug("Unmatched alarm control result: {}", result.request_id)
            return
        entry[1].set_result(result)

    async def alarm_control(
        self,
        hub_id: str,
        alarm_id: str,
        action: AlarmControlAction,
        snooze_minutes: int = 0,
    ) -> AlarmControlResult | None:
        """허브에 AlarmControl 을 보내고 결과를 기다린다.

        Returns:
            허브의 결과. 허브가 오프라인이거나 전송에 실패하거나 시간 안에 답이 없으면 None.
        """
        request_id = uuid.uuid4().hex
        future: asyncio.Future[AlarmControlResult] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (hub_id, future)
        try:
            control = AlarmControl(
                request_id=request_id,
                alarm_id=alarm_id,
                action=action,
                snooze_minutes=snooze_minutes,
            )
            if not await self._connections.send_to_hub(hub_id, Envelope(alarm_control=control)):
                return None
            async with asyncio.timeout(self._timeout):
                result = await future
        except TimeoutError:
            logger.warning("Alarm control timed out: hub={} request={}", hub_id, request_id)
            return None
        finally:
            self._pending.pop(request_id, None)
        if result.ok:
            self._ringing.pop(hub_id, None)
        return result
