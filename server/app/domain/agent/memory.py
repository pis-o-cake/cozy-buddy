"""허브별 단기 대화 기억: 직전 턴의 대상(후속 지시용)과 최근 발화 쌍(LLM 맥락용)."""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.agent.home_view import HomeView
from app.domain.agent.model import (
    CommandParams,
    DeviceAction,
    DeviceCommand,
    Intent,
    SceneCommand,
    StateQuery,
)

MAX_HISTORY_TURNS = 3


@dataclass(frozen=True, slots=True)
class TargetMemory:
    """직전에 성공한 기기·장면·상태 턴의 대상.

    Attributes:
        entity_ids: 대상 엔티티 ID.
        area_id: 대상이 모두 같은 방에 있을 때 그 방. 아니면 빈 문자열.
        domain: 대상이 모두 같은 도메인일 때 그 도메인. 아니면 빈 문자열.
        action: 마지막 기기 동작. 장면·상태 질의면 None.
        params: 마지막 동작의 인자(밝기 등).
    """

    entity_ids: tuple[str, ...]
    area_id: str
    domain: str
    action: DeviceAction | None
    params: CommandParams


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """사용자 발화와 어시스턴트 응답 한 쌍."""

    user: str
    assistant: str


@dataclass(slots=True)
class _Stamped[T]:
    value: T
    expires_at: float


class ConversationMemory:
    """허브당 하나의 대상 기억과 최근 3턴. 프로세스 메모리에만 있으므로 인스턴스를 공유해야 한다."""

    def __init__(
        self,
        target_ttl_seconds: float,
        history_ttl_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._target_ttl = target_ttl_seconds
        self._history_ttl = history_ttl_seconds
        self._monotonic = monotonic
        self._targets: dict[str, _Stamped[TargetMemory]] = {}
        self._history: dict[str, deque[_Stamped[HistoryTurn]]] = {}

    def remember_target(self, hub_id: str, target: TargetMemory) -> None:
        """허브의 직전 대상을 새로 잡는다. 허브를 모르면 섞일 수 있어 기억하지 않는다."""
        if not hub_id:
            return
        self._targets[hub_id] = _Stamped(target, self._monotonic() + self._target_ttl)

    def forget_target(self, hub_id: str) -> None:
        """허브의 직전 대상을 지운다. 무관한 턴이 끼면 "다시"·"또"가 옛 기기를 되풀이하지 않게 한다."""
        self._targets.pop(hub_id, None)

    def target(self, hub_id: str) -> TargetMemory | None:
        """유효 시간 안의 직전 대상. 없거나 만료됐으면 None."""
        stamped = self._targets.get(hub_id)
        if stamped is None:
            return None
        if stamped.expires_at < self._monotonic():
            del self._targets[hub_id]
            return None
        return stamped.value

    def add_turn(self, hub_id: str, user: str, assistant: str) -> None:
        """발화 쌍을 더한다. 오래된 것부터 밀려나 최근 MAX_HISTORY_TURNS 개만 남는다."""
        if not hub_id or not user or not assistant:
            return
        turns = self._history.setdefault(hub_id, deque(maxlen=MAX_HISTORY_TURNS))
        turns.append(_Stamped(HistoryTurn(user, assistant), self._monotonic() + self._history_ttl))

    def recent_turns(self, hub_id: str) -> list[HistoryTurn]:
        """만료되지 않은 최근 발화 쌍을 오래된 순으로."""
        turns = self._history.get(hub_id)
        if not turns:
            return []
        now = self._monotonic()
        return [s.value for s in turns if s.expires_at >= now]


def target_from_intent(intent: Intent, view: HomeView) -> TargetMemory | None:
    """기억할 만한 의도에서 대상을 뽑는다.

    Args:
        intent: 성공적으로 실행된 의도.
        view: 대상 엔티티의 도메인·방을 찾을 집 구조.

    Returns:
        기기 제어·장면·상태 질의가 아니거나 대상을 모르면 None.
    """
    action: DeviceAction | None = None
    params = CommandParams()
    if isinstance(intent, DeviceCommand):
        ids, action, params = intent.entity_ids, intent.action, intent.params
    elif isinstance(intent, SceneCommand):
        ids = (intent.entity_id,)
    elif isinstance(intent, StateQuery):
        ids = intent.entity_ids
    else:
        return None
    entities = [e for i in ids if (e := view.entity(i)) is not None]
    if not ids or len(entities) != len(ids):
        return None
    domains = {e.domain for e in entities}
    areas = {e.area_id or "" for e in entities}
    return TargetMemory(
        ids,
        next(iter(areas)) if len(areas) == 1 else "",
        next(iter(domains)) if len(domains) == 1 else "",
        action,
        params,
    )
