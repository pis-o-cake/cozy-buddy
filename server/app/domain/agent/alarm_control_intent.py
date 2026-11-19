"""울리는 알람을 끄거나 미루는 발화 인식.

허브가 지금 알람을 울리는 중일 때만 쓴다. 울리지 않을 때의 "알람 꺼"는 다음 알람 취소를
뜻하므로 이 모듈이 가로채지 않는다. 발화 전체가 제어 어휘로만 이뤄졌을 때만 해석한다.
"""

import re

from app.domain.agent.model import AlarmControlIntent, AlarmControlKind
from app.domain.agent.normalize import NormalizedText

# 허브가 받는 스누즈 상한과 같다. 더 길게 미루는 말은 새 알람·타이머 요청으로 본다.
MAX_SNOOZE_MINUTES = 60

_MINUTES_RE = re.compile(r"(\d+)\s*분")
_STOP_VERBS = frozenset(
    {
        "꺼",
        "끄",
        "끄기",
        "멈춰",
        "멈춰라",
        "중지",
        "정지",
        "종료",
        "그만",
        "그만둬",
        "됐어",
        "됐다",
        "됐네",
        "취소",
    }
)
_STOP_FILLERS = frozenset({"알람", "타이머", "모닝콜", "소리", "이제", "알람소리"})
_SNOOZE_WORDS = frozenset({"스누즈", "스눌", "snooze"})
_SNOOZE_VERBS = frozenset({"울려", "알려", "깨워"})
_RELATIVE = frozenset(
    {"뒤", "뒤에", "후", "후에", "이따", "이따가", "있다", "있다가", "나중에", "잠깐", "잠시"}
)
_SNOOZE_FILLERS = frozenset({"알람", "만", "한번", "또", "다시"})


def _stop(words: list[str]) -> bool:
    # "알람 취소"는 다음 알람 취소와 구분되지 않으므로 "취소"만 말했을 때만 멈춤으로 본다.
    if "취소" in words and words != ["취소"]:
        return False
    if not any(w in _STOP_VERBS for w in words):
        return False
    return all(w in _STOP_VERBS or w in _STOP_FILLERS for w in words)


def _snooze(norm: NormalizedText) -> AlarmControlIntent | None:
    minutes_found = _MINUTES_RE.findall(norm.text)
    rest = _MINUTES_RE.sub(" ", norm.text).split()
    allowed = _SNOOZE_WORDS | _SNOOZE_VERBS | _RELATIVE | _SNOOZE_FILLERS
    if not rest and not minutes_found:
        return None
    if len(minutes_found) > 1 or any(w not in allowed for w in rest):
        return None
    again = "다시" in rest
    explicit = any(w in _SNOOZE_WORDS for w in rest)
    delayed = bool(minutes_found) or any(w in _RELATIVE for w in rest)
    asked = any(w in _SNOOZE_VERBS for w in rest)
    # "5분 뒤에 알람 울려" 처럼 "다시" 가 없으면 새 알람 요청이다.
    if not (explicit or (again and (asked or delayed))):
        return None
    if not minutes_found:
        return AlarmControlIntent(AlarmControlKind.SNOOZE)
    minutes = int(minutes_found[0])
    if not 1 <= minutes <= MAX_SNOOZE_MINUTES:
        return None
    return AlarmControlIntent(AlarmControlKind.SNOOZE, minutes)


def match_alarm_control(norm: NormalizedText) -> AlarmControlIntent | None:
    """울리는 알람의 정지·스누즈 발화면 의도를 돌려준다.

    Args:
        norm: 정규화된 발화.

    Returns:
        정지("알람 꺼", "그만", "됐어 꺼")나 스누즈("5분 뒤에 다시", "스누즈")가 아니면 None.
    """
    words = norm.text.split()
    if not words or len(words) > 5:
        return None
    if _stop(words):
        return AlarmControlIntent(AlarmControlKind.STOP)
    return _snooze(norm)
