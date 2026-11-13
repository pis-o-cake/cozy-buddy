"""Transcript normaliser shared by every STT variant so exact-match rates are comparable."""
from __future__ import annotations

import re

_DIGIT = {"영": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_SINO_RUN = re.compile(
    r"(?<![가-힣])([영일이삼사오육칠팔구십백]+)(?=\s*(?:시|분|초|퍼센트|프로|%|곱하기|은|는|으로|\s|$))"
)
_PUNCT = re.compile(r"[\s.,!?~。…'\"]")


def sino_to_int(word: str) -> int:
    """Convert a Sino-Korean numeral such as '삼십오' to 35."""
    total, cur = 0, 0
    for ch in word:
        if ch in _DIGIT:
            cur = _DIGIT[ch]
        elif ch == "십":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "백":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def normalize_numbers(text: str) -> str:
    """Rewrite Sino-Korean number words (with unit context) as digits."""
    return _SINO_RUN.sub(lambda m: str(sino_to_int(m.group(1))), text)


def normalize(text: str) -> str:
    """Ignore punctuation, spaces and digit vs hangul number spelling."""
    text = normalize_numbers(text).replace("%", "퍼센트").replace("프로", "퍼센트")
    return _PUNCT.sub("", text)
