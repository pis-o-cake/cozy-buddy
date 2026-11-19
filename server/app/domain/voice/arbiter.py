"""다중 허브 중재: 여러 허브가 같은 발화를 들었을 때 한 허브만 처리하게 한다.

흐름은 `begin`(UtteranceEnd 수신) → STT → `decide`(전사 직후, 이해 단계 전)다.
`begin` 이 먼저 불리므로 다른 허브의 발화가 아직 STT 중인지 알 수 있고, 그럴 때만 잠깐 기다린다.
기다릴 상대가 없으면 `decide` 는 대기 없이 바로 돌아오므로 단독 허브의 지연은 0이다.

Note:
    판정 시각은 허브 시계가 아니라 서버가 UtteranceEnd 를 받은 monotonic 시각이다.
    허브 간 시계 오차를 신경 쓰지 않아도 되지만, 허브별 네트워크 지연만큼 오차가 생긴다.
"""

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from loguru import logger

DEFAULT_HOME = ""
# 발화 종료 시각 차이가 이 안이면 같은 발화 후보다.
DEFAULT_WINDOW_SECONDS = 1.2
# 편집 거리 기준 유사도(1 - 거리/긴 쪽 길이)가 이 이상이면 같은 말이다.
DEFAULT_SIMILARITY = 0.8
# 더 일찍 끝난 발화가 아직 STT 중일 때 최대로 기다리는 시간.
DEFAULT_HOLD_SECONDS = 0.3
# 정규화 후 이 글자 수 미만의 짧은 문장은 완전히 같을 때만 같은 말로 본다.
# 한 글자만 달라도 비율이 0.8 을 넘는 짧은 명령("거실 조명 꺼줘" / "침실 조명 꺼줘")을
# 중복으로 오판하지 않으려는 것이다.
FUZZY_MIN_CHARS = 10
# 판정 기록을 보관하는 시간. 창(1.2초)과 보류(0.3초)보다 충분히 길게 둔다.
RETAIN_SECONDS = 10.0

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """비교용 정규화: NFC, 소문자, 공백·문장부호 제거."""
    return _NON_WORD.sub("", unicodedata.normalize("NFC", text).casefold())


def similarity(a: str, b: str) -> float:
    """정규화한 두 문장의 편집 거리 유사도(0..1). 둘 다 비면 0.

    difflib.SequenceMatcher 대신 Levenshtein 을 쓰는 이유: 설계가 "편집 거리 비율"로 정의했고,
    SequenceMatcher 는 입력 순서에 따라 값이 달라질 수 있어 짧은 한국어 문장에서 예측하기 어렵다.
    문장이 짧아(수십 자) O(nm) 비용은 무시할 만하다.
    """
    left, right = normalize(a), normalize(b)
    longest = max(len(left), len(right))
    if longest == 0:
        return 0.0
    if left == right:
        return 1.0
    return 1.0 - _levenshtein(left, right) / longest


def is_same_text(a: str, b: str, threshold: float = DEFAULT_SIMILARITY) -> bool:
    """같은 말로 볼 수 있는가. 짧은 문장은 정규화 후 동일할 때만, 긴 문장은 유사도 기준으로 본다."""
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return False
    if left == right:
        return True
    if max(len(left), len(right)) < FUZZY_MIN_CHARS:
        return False
    return similarity(left, right) >= threshold


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


@dataclass(slots=True)
class Decision:
    """중재 결과. `winner` 가 거짓이면 `winner_hub_id` 의 허브가 이미 처리했다."""

    winner: bool
    winner_hub_id: str = ""


@dataclass(slots=True, eq=False)
class Ticket:
    """STT 가 진행 중인 발화 한 건. `UtteranceArbiter.begin` 이 만든다."""

    hub_id: str
    home_id: str
    ended_at: float
    transcript: str = ""
    won: bool = False
    resolved: asyncio.Event = field(default_factory=asyncio.Event)
    decided_at: float = 0.0


class UtteranceArbiter:
    """집(home) 단위로 최근 발화를 기억하고 중복을 거른다.

    Args:
        window_seconds: 두 발화의 종료 시각 차이 허용치.
        similarity_threshold: 같은 말로 볼 최소 유사도.
        hold_seconds: 더 일찍 끝난 상대의 STT 를 기다리는 최대 시간.
        clock: 기록 보관 기한 판단용 monotonic 시계.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        similarity_threshold: float = DEFAULT_SIMILARITY,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._threshold = similarity_threshold
        self._hold = hold_seconds
        self._clock = clock
        self._tickets: dict[str, list[Ticket]] = {}

    def begin(self, hub_id: str, ended_at: float, home_id: str = DEFAULT_HOME) -> Ticket:
        """발화 종료를 등록한다. STT 를 시작하기 전에 불러야 한다."""
        ticket = Ticket(hub_id=hub_id, home_id=home_id, ended_at=ended_at)
        tickets = self._tickets.setdefault(home_id, [])
        self._prune(tickets)
        tickets.append(ticket)
        return ticket

    def abandon(self, ticket: Ticket) -> None:
        """처리하지 않기로 한 발화를 놓는다(취소, STT 실패, 빈 전사). 여러 번 불러도 된다."""
        ticket.resolved.set()

    async def decide(self, ticket: Ticket, transcript: str) -> Decision:
        """전사문이 나온 뒤 이 발화를 처리할지 정한다.

        더 일찍 끝난 다른 허브의 발화가 아직 STT 중이면 최대 `hold_seconds` 기다린다.
        트레이드오프: 보류는 "나중에 끝난 발화의 STT 가 먼저 끝난" 경우에만, 그 발화에만 걸린다.
        이른 발화는 절대 기다리지 않는다. 보류를 넘겨서 먼저 처리가 나간 뒤 이른 발화가 도착하면
        그쪽이 거절된다(드문 경우이며, 같은 말에 두 번 답하는 것보다 낫다).
        """
        ticket.transcript = transcript
        try:
            await self._hold_for_earlier(ticket)
            return self._resolve(ticket)
        finally:
            ticket.decided_at = self._clock()
            ticket.resolved.set()

    async def _hold_for_earlier(self, ticket: Ticket) -> None:
        pending = [
            asyncio.ensure_future(t.resolved.wait())
            for t in self._peers(ticket)
            if not t.resolved.is_set() and t.ended_at < ticket.ended_at
        ]
        if not pending:
            return
        try:
            await asyncio.wait(pending, timeout=self._hold)
        finally:
            for task in pending:
                task.cancel()

    def _resolve(self, ticket: Ticket) -> Decision:
        for other in self._peers(ticket):
            if not other.won or not self._same_utterance(ticket, other):
                continue
            logger.info(
                "Utterance declined as duplicate: hub={} winner={} gap_ms={}",
                ticket.hub_id,
                other.hub_id,
                round(abs(ticket.ended_at - other.ended_at) * 1000),
            )
            return Decision(winner=False, winner_hub_id=other.hub_id)
        ticket.won = True
        return Decision(winner=True)

    def _peers(self, ticket: Ticket) -> list[Ticket]:
        tickets = self._tickets.get(ticket.home_id, [])
        return [t for t in tickets if t is not ticket and t.hub_id != ticket.hub_id]

    def _same_utterance(self, ticket: Ticket, other: Ticket) -> bool:
        if abs(ticket.ended_at - other.ended_at) > self._window:
            return False
        return is_same_text(ticket.transcript, other.transcript, self._threshold)

    def _prune(self, tickets: list[Ticket]) -> None:
        now = self._clock()
        tickets[:] = [
            t for t in tickets if not t.resolved.is_set() or now - t.decided_at < RETAIN_SECONDS
        ]
