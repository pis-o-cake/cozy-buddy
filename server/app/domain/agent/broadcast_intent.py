"""허브 간 방송 요청 인식. "주방에 밥 먹으라고 방송해줘" 같은 발화를 BroadcastIntent 로 푼다.

방 이름은 집 구조를 보지 않고 말한 그대로 넘긴다. 어느 허브인지는 실행 쪽이 허브 등록 정보로 푼다.
"""

import re

from app.domain.agent.model import BroadcastIntent
from app.domain.agent.normalize import NormalizedText

_MAX_WORDS = 30
_MAX_TEXT_CHARS = 200

# 정규화가 요청 어미("해줘")를 뗀 뒤 끝에 남는 "방송".
_BROADCAST_END_RE = re.compile(r"^(?P<body>.+?)\s*방송(?:\s*(?:부탁|하자|해라))?$")
_ALL_TARGET_RE = re.compile(
    r"^(?:모두|전체|전부|집\s*전체|온\s*집|모든\s*(?:허브|방))(?:에게|한테|께|에)?\s+(?P<message>.+)$"
)
_LEADING_TARGET_RE = re.compile(r"^(?P<target>\S{1,10}?)(?:에게|한테|에)\s+(?P<message>.+)$")
_TRAILING_TARGET_RE = re.compile(r"^(?P<message>.+?라고)\s+(?P<target>\S{1,10}?)(?:에게|한테|에)$")
# "저녁에 밥 먹으라고 방송" 의 "저녁에" 같은 때 표현은 방이 아니다.
_TIME_WORDS = frozenset(
    {
        "아침",
        "점심",
        "저녁",
        "낮",
        "밤",
        "새벽",
        "오늘",
        "내일",
        "지금",
        "나중",
        "이따",
        "곧",
        "일찍",
    }
)
# 말이 아닌 명사구("축구 중계 방송")를 걸러내려고 문장 끝 모양을 본다.
_SENTENCE_END_RE = re.compile(r"(?:고|요|어|아|자|라|다|해|야|까)$")
_IMPERATIVE_QUOTE_RE = re.compile(r"(으)?라고$")
_PROPOSAL_QUOTE_RE = re.compile(r"[자다]고$")


def _spoken(message: str) -> str:
    """간접 인용(먹으라고)을 방송에 읽을 문장(먹으세요)으로 바꾼다."""
    if (quote := _IMPERATIVE_QUOTE_RE.search(message)) is not None:
        ending = "으세요" if quote.group(1) else "세요"
        return message[: quote.start()] + ending
    if _PROPOSAL_QUOTE_RE.search(message):
        return message[:-1]
    return message


def _split_target(body: str) -> tuple[str | None, str]:
    """(대상, 메시지). 대상을 말하지 않았거나 "모두"류면 대상은 None(말한 허브 외 전체)."""
    if (match := _ALL_TARGET_RE.match(body)) is not None:
        return None, match["message"]
    for pattern in (_LEADING_TARGET_RE, _TRAILING_TARGET_RE):
        match = pattern.match(body)
        if match is not None and match["target"] not in _TIME_WORDS:
            return match["target"], match["message"]
    return None, body


def match_broadcast(norm: NormalizedText) -> BroadcastIntent | None:
    """방송 요청이면 의도를 돌려준다.

    Args:
        norm: 정규화된 발화.

    Returns:
        방송 요청이 아니거나 읽을 문장이 없으면 None. 그러면 LLM 툴이 이어받는다.
    """
    if len(norm.text.split()) > _MAX_WORDS:
        return None
    ended = _BROADCAST_END_RE.match(norm.text)
    if ended is None:
        return None
    target, message = _split_target(ended["body"].strip())
    message = message.strip()
    if not message or len(message) > _MAX_TEXT_CHARS or not _SENTENCE_END_RE.search(message):
        return None
    return BroadcastIntent(target, _spoken(message))
