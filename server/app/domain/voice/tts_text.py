"""TTS 입력 전처리: 한국어 숫자·단위 정규화와 문장 분리."""

import re

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_CELSIUS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:°\s*C|℃)")
_DEGREE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*°")
_UNIT_RES = (
    (re.compile(r"(\d)\s*kWh(?![A-Za-z])", re.IGNORECASE), r"\1킬로와트시"),
    (re.compile(r"(\d)\s*kW(?![A-Za-z])", re.IGNORECASE), r"\1킬로와트"),
    (re.compile(r"(\d)\s*W(?![A-Za-z])"), r"\1와트"),
    (re.compile(r"(\d)\s*km(?![A-Za-z])", re.IGNORECASE), r"\1킬로미터"),
    (re.compile(r"(\d)\s*cm(?![A-Za-z])", re.IGNORECASE), r"\1센티미터"),
    (re.compile(r"(\d)\s*mm(?![A-Za-z])", re.IGNORECASE), r"\1밀리미터"),
    (re.compile(r"(\d)\s*kg(?![A-Za-z])", re.IGNORECASE), r"\1킬로그램"),
)
_MARKDOWN_RE = re.compile(r"[*_`#>]+|^\s*[-•]\s+", re.MULTILINE)
_SPACE_RE = re.compile(r"[ \t]+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?。…])\s+|\n+")
_MINUTE_ZERO = "00"


def _time_to_korean(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), match.group(2)
    if minute == _MINUTE_ZERO:
        return f"{hour}시"
    return f"{hour}시 {int(minute)}분"


def normalize_for_tts(text: str) -> str:
    """TTS 가 어색하게 읽는 표기를 한국어 읽기로 바꾼다.

    예: "50%" -> "50퍼센트", "7:30" -> "7시 30분", "23.5°C" -> "23.5도".

    Args:
        text: 자막과 같은 원문 한 문장.

    Returns:
        음성 합성에 넘길 문장. 자막에는 쓰지 않는다.
    """
    result = _MARKDOWN_RE.sub("", text)
    result = _PERCENT_RE.sub(r"\1퍼센트", result)
    result = _TIME_RE.sub(_time_to_korean, result)
    result = _CELSIUS_RE.sub(r"\1도", result)
    result = _DEGREE_RE.sub(r"\1도", result)
    for pattern, replacement in _UNIT_RES:
        result = pattern.sub(replacement, result)
    return _SPACE_RE.sub(" ", result).strip()


def split_sentences(buffer: str, *, final: bool) -> tuple[list[str], str]:
    """버퍼에서 완성된 문장을 떼어낸다.

    Args:
        buffer: 지금까지 쌓인 텍스트.
        final: True 면 남은 꼬리도 문장으로 내보낸다.

    Returns:
        (완성된 문장 목록, 아직 끝나지 않은 꼬리).
    """
    parts = _SENTENCE_END_RE.split(buffer)
    if final:
        return [p.strip() for p in parts if p.strip()], ""
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]
