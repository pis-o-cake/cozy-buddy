"""날씨 질문 인식. 기기 매칭이 실패한 발화에만 쓴다.

"거실 온도 몇 도야" 같은 실내 센서 질의는 기기 매처가 먼저 가져가므로, 여기서는 집 센서로
해석되지 않은 발화만 본다. 그래서 "지금 몇 도야" 는 실내 센서 단어가 없을 때만 바깥 기온이다.
"""

import re

from app.domain.agent.model import WeatherIntent
from app.domain.agent.normalize import NormalizedText
from app.domain.weather.schemas import WeatherWhen

# 긴 잡담이 날씨 질문으로 오인되지 않게 하는 단어 수 상한.
_MAX_WORDS = 7

_EXPLICIT_RE = re.compile(
    r"날씨|기온|우산|(?:밖|바깥|외부)\s*(?:은|의)?\s*(?:온도|기온|몇\s*도)"
    r"|(?<![가-힣])(?:비|눈)\s*(?:와|오|올|내려|내리)"
)
_BARE_TEMPERATURE_RE = re.compile(r"몇\s*도")
# 이 단어가 있으면 바깥이 아니라 실내·기기 온도를 뜻할 수 있다.
_INDOOR_RE = re.compile(r"온도|습도|에어컨|히터|보일러|난방|냉방|설정")
# 예보를 두 날까지만 갖고 있으므로 더 먼 날은 해석하지 않는다.
_OUT_OF_RANGE_RE = re.compile(r"모레|글피|이번\s*주|다음\s*주|주말|일주일|[0-9]+\s*일\s*(?:뒤|후)")


def _when(norm: NormalizedText, raw: str, default: WeatherWhen) -> WeatherWhen:
    if "내일" in norm.text:
        return WeatherWhen.TOMORROW
    if "오늘" in norm.text:
        return WeatherWhen.TODAY
    if re.search(r"지금|현재", raw):
        return WeatherWhen.NOW
    return default


def match_weather(norm: NormalizedText, raw: str) -> WeatherIntent | None:
    """날씨 질문이면 의도를 돌려준다.

    Args:
        norm: 정규화된 발화.
        raw: 정규화 전 원문. "지금" 은 정규화에서 군말로 지워지므로 여기서 본다.

    Returns:
        날씨 질문이 아니거나 해석할 수 없는 시점이면 None.
    """
    if len(norm.text.split()) > _MAX_WORDS or _OUT_OF_RANGE_RE.search(norm.text):
        return None
    if _EXPLICIT_RE.search(norm.text):
        outdoor_temperature = re.search(r"밖|바깥|외부", norm.text) is not None
        default = WeatherWhen.NOW if outdoor_temperature else WeatherWhen.TODAY
        return WeatherIntent(_when(norm, raw, default))
    if _BARE_TEMPERATURE_RE.search(norm.text) and not _INDOOR_RE.search(norm.text):
        return WeatherIntent(_when(norm, raw, WeatherWhen.NOW))
    return None
