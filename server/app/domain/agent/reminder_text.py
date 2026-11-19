"""리마인더가 울릴 때 읽어 줄 문장. 순수 함수만 둔다."""

from collections.abc import Callable

from app.domain.agent.reminder_intent import LABEL_SUFFIX

KEY_FIRED = "agent.reminder.fired"

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JONG_COUNT = 28
_JONG_RIEUL = 8


def honorific_what(label: str) -> str:
    """label 을 "~실 시간이에요" 앞에 붙일 말로 바꾼다.

    "병원 가기" 처럼 동작 + "기" 로 저장된 내용은 어간에 높임 "(으)시" 를 붙여 "병원 가실" 로
    만든다. 받침이 있으면 "먹으실", ㄹ 받침은 탈락해 "만드실" 이 된다. 그 밖의 내용(명사구 등)은
    그대로 둔다.

    Args:
        label: 리마인더 label.

    Returns:
        "시간이에요" 앞에 이어 붙일 말.
    """
    if len(label) < 2 or not label.endswith(LABEL_SUFFIX):
        return label
    stem = label[: -len(LABEL_SUFFIX)]
    last = ord(stem[-1])
    if not _HANGUL_BASE <= last <= _HANGUL_LAST:
        return label
    jong = (last - _HANGUL_BASE) % _JONG_COUNT
    if jong == 0:
        return f"{stem}실"
    if jong == _JONG_RIEUL:
        return f"{stem[:-1]}{chr(last - jong)}실"
    return f"{stem}으실"


def fired_text(label: str, translate: Callable[[str], str]) -> str:
    """리마인더가 울렸을 때 읽는 문장. 예: "병원 가실 시간이에요"."""
    return translate(KEY_FIRED).format(what=honorific_what(label.strip()))
