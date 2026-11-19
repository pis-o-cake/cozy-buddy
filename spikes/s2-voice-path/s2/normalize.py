"""Transcript normaliser shared by every STT variant so exact-match rates are comparable."""
from __future__ import annotations

import re

_DIGIT = {"영": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_SINO_RUN = re.compile(
    r"(?<![가-힣])([영일이삼사오육칠팔구십백]+)(?=\s*(?:시|분|초|퍼센트|프로|%|곱하기|은|는|으로|\s|$))"
)
_NATIVE_HOUR = {
    "하나": 1, "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
    "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12,
}
# Longest alternatives first so '열두' is not read as '열'.
_NATIVE_HOUR_RE = re.compile(
    r"(?<![가-힣])(열두|열한|하나|다섯|여섯|일곱|여덟|아홉|열|한|두|세|네)\s*(?=시)"
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


def normalize_native_hours(text: str) -> str:
    """Rewrite native Korean hour words before '시' (일곱 시, 열두 시) as digits."""
    return _NATIVE_HOUR_RE.sub(lambda m: str(_NATIVE_HOUR[m.group(1)]), text)


def normalize_legacy(text: str) -> str:
    """Normaliser used up to 2026-11-19 (no native Korean hour forms); kept to compare old figures."""
    text = normalize_numbers(text).replace("%", "퍼센트").replace("프로", "퍼센트")
    return _PUNCT.sub("", text)


def normalize(text: str) -> str:
    """Ignore punctuation, spaces and digit vs hangul number spelling, incl. native hour words."""
    text = normalize_numbers(normalize_native_hours(text)).replace("%", "퍼센트").replace("프로", "퍼센트")
    return _PUNCT.sub("", text)
