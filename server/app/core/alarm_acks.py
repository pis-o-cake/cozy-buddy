"""허브에 보낸 알람 변경의 AlarmAck 대기."""

import asyncio

from cozy.v1.alarm_pb2 import AlarmAck
from loguru import logger

DEFAULT_ACK_TIMEOUT_SECONDS = 3.0


class AlarmAckWaiters:
    """request_id 로 허브의 AlarmAck 를 기다리는 요청을 추적한다. 메모리에만 있다.

    Attributes:
        timeout_seconds: 호출자가 ack 를 기다리는 시간. 넘기면 미확정으로 다룬다.
    """

    def __init__(self, timeout_seconds: float = DEFAULT_ACK_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, tuple[str, asyncio.Future[AlarmAck]]] = {}

    def expect(self, hub_id: str, request_id: str) -> asyncio.Future[AlarmAck]:
        """ack 를 기다릴 future 를 등록한다. 요청을 보내기 전에 호출하고, 끝나면 `discard` 한다."""
        future: asyncio.Future[AlarmAck] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (hub_id, future)
        return future

    def discard(self, request_id: str) -> None:
        """대기 등록을 지운다."""
        self._pending.pop(request_id, None)

    def resolve(self, hub_id: str, ack: AlarmAck) -> None:
        """허브의 ack 를 기다리는 요청에 전달한다. 모르는 요청이나 다른 허브의 ack 는 무시한다."""
        entry = self._pending.get(ack.request_id)
        if entry is None or entry[0] != hub_id or entry[1].done():
            logger.debug("Unmatched alarm ack: hub={} request={}", hub_id, ack.request_id)
            return
        entry[1].set_result(ack)
