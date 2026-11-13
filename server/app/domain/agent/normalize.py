"""한국어 발화 정규화. 순수 함수만 둔다."""

import re
import unicodedata
from dataclasses import dataclass

_NATIVE_NUMBERS = {
    "열두": 12,
    "열한": 11,
    "열": 10,
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
}
_SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}

# 시/시간 앞의 고유어 수사만 숫자로 바꾼다. 다른 문맥의 "한", "두" 는 건드리지 않는다.
_NATIVE_RE = re.compile(
    r"(?<![가-힣])(" + "|".join(sorted(_NATIVE_NUMBERS, key=len, reverse=True)) + r")(?=\s*시)"
)
# 분/초/퍼센트 앞의 한자어 수사. 빈 매치는 치환 함수에서 그대로 둔다.
_SINO_RE = re.compile(
    r"(?:(?<![가-힣])|(?<=[시간]))"
    r"(백|(?:[일이삼사오육칠팔구]?십)?[일이삼사오육칠팔구]?)"
    r"(?=\s*(?:분|초|%|퍼센트|프로))"
)
_COLON_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_PUNCT_RE = re.compile(r"[^\w\s%]|_")
_PERCENT_RE = re.compile(r"(\d)\s*(?:퍼센트|프로)")
_UNIT_GAP_RE = re.compile(r"(\d)\s+(시간|시|분|초|%)")
_SPACE_RE = re.compile(r"\s+")

# 요청형 어미. "켜줘", "켜 줘요", "꺼 주세요", "밝게 해줄래요" 등의 꼬리를 뗀다.
_REQUEST_END_RE = re.compile(
    r"(?:\s*해)?\s*(?:줘요?|주세요|주라|줄래요?|주실래요?|주시겠어요?"
    r"|줄 수 있(?:어요?|니|을까요?)|봐요?)\s*$"
)
_TRAILING_RE = re.compile(r"(?<=[가-힣0-9%])\s*(해요?|요)\s*$")

_FILLERS = frozenset(
    {
        "좀", "제발", "한번", "그냥", "빨리", "얼른", "지금", "당장", "바로", "어서", "혹시",
        "저기", "있잖아", "이", "그", "저", "우리", "더", "조금", "약간", "살짝", "아주",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """정규화된 발화.

    Attributes:
        text: 공백으로 단어를 나눈 형태. 수사는 아라비아 숫자, 요청 어미와 군말은 제거됨.
        compact: text 에서 공백을 모두 뗀 형태. 어휘 스캔에 쓴다.
        requested: 요청 어미("~해줘", "~주세요", 끝의 "해")를 떼었는지 여부.
    """

    text: str
    compact: str
    requested: bool


def compact_form(value: str) -> str:
    """이름·별칭 비교용으로 공백·기호를 없애고 소문자로 맞춘다."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).lower())


def entity_base_name(name: str, area_name: str | None) -> str:
    """엔티티 이름에서 방 이름 접두어를 뗀다. 떼고 남는 게 없으면 원래 이름을 돌려준다."""
    if not area_name or not name.startswith(area_name):
        return name
    rest = name[len(area_name) :].strip()
    return rest or name


def _sino_to_int(value: str) -> int:
    if value == "백":
        return 100
    if "십" not in value:
        return _SINO_DIGITS[value]
    left, right = value.split("십")
    tens = _SINO_DIGITS[left] if left else 1
    return tens * 10 + (_SINO_DIGITS[right] if right else 0)


def _replace_sino(match: re.Match[str]) -> str:
    value = match.group(1)
    return str(_sino_to_int(value)) if value else ""


def _colon_to_korean(match: re.Match[str]) -> str:
    hour, minute = match.group(1), int(match.group(2))
    return f"{hour}시" if minute == 0 else f"{hour}시 {minute}분"


def _strip_endings(text: str) -> tuple[str, bool]:
    requested = False
    while True:
        stripped = _REQUEST_END_RE.sub("", text, count=1)
        if stripped != text:
            text, requested = stripped.rstrip(), True
            continue
        match = _TRAILING_RE.search(text)
        if match is None:
            return text, requested
        requested = requested or match.group(1).startswith("해")
        text = text[: match.start()].rstrip()


def normalize(text: str) -> NormalizedText:
    """발화를 매처가 다루기 쉬운 형태로 바꾼다.

    공백·문장부호 정리, 수사의 숫자 변환, 군말과 요청 어미 제거를 한다.
    조사는 어휘를 아는 매처가 명사에 붙은 것만 떼므로 여기서는 건드리지 않는다.
    """
    value = unicodedata.normalize("NFKC", text).lower()
    value = _COLON_TIME_RE.sub(_colon_to_korean, value)
    value = _PUNCT_RE.sub(" ", value)
    value = _NATIVE_RE.sub(lambda m: str(_NATIVE_NUMBERS[m.group(1)]), value)
    value = _SINO_RE.sub(_replace_sino, value)
    value = _PERCENT_RE.sub(r"\1%", value)
    value = _UNIT_GAP_RE.sub(r"\1\2", value)
    words = [w for w in _SPACE_RE.split(value.strip()) if w and w not in _FILLERS]
    value, requested = _strip_endings(" ".join(words))
    value = _SPACE_RE.sub(" ", value).strip()
    return NormalizedText(text=value, compact=value.replace(" ", ""), requested=requested)
