"""기기·장면 명령용 어휘와 스캐너.

공백을 뗀 발화 전체를 어휘로 남김없이 쪼갠다. 쪼개지지 않는 조각이 하나라도 있으면
스캔이 실패하고 매처는 NoMatch 를 낸다. "불고기"가 "불"+"고기"로 오해되지 않는 근거다.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.agent.model import HomeModel
from app.domain.agent.normalize import compact_form, entity_base_name


class Kind(StrEnum):
    """어휘 조각의 종류."""

    AREA = "area"
    HERE = "here"
    GLOBAL = "global"
    ALL = "all"
    DOMAIN = "domain"
    ENTITY = "entity"
    SCENE = "scene"
    SCENE_WORD = "scene_word"
    SCENE_VERB = "scene_verb"
    PARTICLE = "particle"
    FILLER = "filler"
    ASK_END = "ask_end"
    ON = "on"
    OFF = "off"
    TOGGLE = "toggle"
    OPEN = "open"
    CLOSE = "close"
    UP = "up"
    DOWN = "down"
    BRIGHTER = "brighter"
    DARKER = "darker"
    SET = "set"
    MAX = "max"
    MIN = "min"
    SUPER = "super"
    BRIGHTNESS = "brightness"
    PCT = "pct"
    NUM = "num"
    Q_ON = "q_on"
    Q_OFF = "q_off"
    Q_OPEN = "q_open"
    Q_CLOSED = "q_closed"
    Q_STATE = "q_state"
    ASK = "ask"


VERB_KINDS = frozenset(
    {
        Kind.ON, Kind.OFF, Kind.TOGGLE, Kind.OPEN, Kind.CLOSE, Kind.UP, Kind.DOWN,
        Kind.BRIGHTER, Kind.DARKER, Kind.SET,
    }
)  # fmt: skip
QUERY_KINDS = frozenset({Kind.Q_ON, Kind.Q_OFF, Kind.Q_OPEN, Kind.Q_CLOSED, Kind.Q_STATE})
_NOMINAL = frozenset(
    {
        Kind.AREA, Kind.HERE, Kind.GLOBAL, Kind.ALL, Kind.DOMAIN, Kind.ENTITY, Kind.SCENE,
        Kind.SCENE_WORD, Kind.PCT, Kind.NUM, Kind.BRIGHTNESS, Kind.MAX, Kind.MIN,
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Lex:
    """스캔 결과 조각.

    Attributes:
        kind: 종류.
        surface: 발화에서 차지한 글자(공백 제거본).
        ids: AREA/ENTITY/SCENE 가 가리키는 id 들.
        domain: DOMAIN 의 HA 도메인.
        hint: DOMAIN 이 요구하는 이름 조각. 있으면 엔티티 이름에 반드시 들어 있어야 한다.
        number: PCT/NUM 의 값.
    """

    kind: Kind
    surface: str
    ids: tuple[str, ...] = ()
    domain: str | None = None
    hint: str | None = None
    number: int | None = None


def _words(kind: Kind, *surfaces: str) -> dict[str, Lex]:
    return {s: Lex(kind, s) for s in surfaces}


def _domain_words(domain: str, *surfaces: str, hint: str | None = None) -> dict[str, Lex]:
    return {s: Lex(Kind.DOMAIN, s, domain=domain, hint=hint) for s in surfaces}


def _query_forms(kind: Kind, stems: tuple[str, ...]) -> dict[str, Lex]:
    endings = ("", "어", "나", "니", "냐", "지", "는지", "을까", "는가")
    return _words(kind, *(stem + end for stem in stems for end in endings))


def _build_static() -> dict[str, Lex]:
    table: dict[str, Lex] = {}
    table.update(_domain_words("light", "불", "조명", "등", "라이트", "전등", "램프", "형광등"))
    table.update(_domain_words("light", "무드등", "간접등", "간접조명", "조명등"))
    table.update(_domain_words("light", "스탠드", hint="스탠드"))
    table.update(_domain_words("switch", "플러그", "콘센트", "멀티탭", "스위치"))
    table.update(_domain_words("switch", "포트", "전기포트", hint="포트"))
    table.update(_domain_words("fan", "선풍기", "서큘레이터", "환풍기"))
    table.update(_domain_words("cover", "커튼", "블라인드", "롤스크린"))
    table.update(_domain_words("climate", "에어컨", "에어콘", "난방", "냉방", "보일러", "히터"))
    table.update(_domain_words("sensor", "온도", "기온", hint="온도"))
    table.update(_domain_words("sensor", "습도", hint="습도"))

    table.update(_words(Kind.ALL, "전부", "모두", "다", "모든", "전체"))
    table.update(_words(Kind.GLOBAL, "집", "집안", "온집", "집전체"))
    table.update(_words(Kind.HERE, "여기", "방", "이방", "방안", "이곳", "우리방", "지금방"))
    table.update(_words(Kind.FILLER, "좀", "그", "이", "저", "우리", "더", "조금", "약간"))
    table.update(_words(Kind.FILLER, "그리고", "지금", "제발", "빨리", "얼른", "그냥"))

    table.update(_words(Kind.ON, "켜", "켜라", "켜자", "켤래", "켜세요", "켬", "틀어", "틀어라"))
    table.update(_words(Kind.ON, "틀자", "틀래", "작동", "가동"))
    table.update(_words(Kind.OFF, "꺼", "꺼라", "끄", "끄자", "끌래", "끄세요", "끔", "소등"))
    table.update(_words(Kind.OFF, "멈춰", "멈춰라", "정지", "중지", "종료"))
    table.update(_words(Kind.TOGGLE, "토글", "반전"))
    table.update(_words(Kind.OPEN, "열어", "열어라", "열자", "열래", "열기", "걷어", "걷자"))
    table.update(_words(Kind.CLOSE, "닫아", "닫아라", "닫자", "닫을래", "닫기", "쳐", "쳐라"))
    table.update(_words(Kind.UP, "올려", "올려라", "올리", "높여", "높여라"))
    table.update(_words(Kind.DOWN, "내려", "내려라", "낮춰", "낮춰라", "낮추"))
    table.update(_words(Kind.BRIGHTER, "밝게", "밝혀", "환하게", "밝아지게"))
    table.update(_words(Kind.DARKER, "어둡게", "어둡히", "약하게", "은은하게", "어두워지게"))
    table.update(_words(Kind.SET, "맞춰", "맞춰라", "설정", "조절", "조정", "바꿔", "변경", "세팅"))
    table.update(_words(Kind.MAX, "최대", "최대로", "최고", "풀"))
    table.update(_words(Kind.MIN, "최소", "최소로", "최저"))
    table.update(_words(Kind.SUPER, "가장", "제일"))
    table.update(_words(Kind.BRIGHTNESS, "밝기"))
    table.update(_words(Kind.SCENE_WORD, "장면", "모드", "씬", "시나리오"))
    table.update(_words(Kind.SCENE_VERB, "실행", "적용", "발동", "시작", "활성화", "활성"))

    table.update(_query_forms(Kind.Q_ON, ("켜져있", "켜졌", "켜진")))
    table.update(_query_forms(Kind.Q_OFF, ("꺼져있", "꺼졌", "꺼진")))
    table.update(_query_forms(Kind.Q_OPEN, ("열려있", "열렸", "열린")))
    table.update(_query_forms(Kind.Q_CLOSED, ("닫혀있", "닫혔", "닫힌")))
    table.update(_words(Kind.Q_STATE, "상태"))
    table.update(_words(Kind.ASK, "몇도", "몇%", "몇", "알려", "어때", "얼마"))
    return table


STATIC_LEXICON = _build_static()

PARTICLES = frozenset(
    {
        "을", "를", "이", "가", "은", "는", "에", "에서", "의", "로", "으로", "도", "만",
        "에는", "에서는", "에서도", "에도", "으로는", "랑", "이랑", "하고", "와", "과",
    }
)  # fmt: skip
ASK_ENDS = frozenset(
    {"야", "예", "이야", "인가", "인지", "니", "냐", "지", "일까", "나", "가", "이에"}
)
_PCT_RE = re.compile(r"\d{1,3}%")
_NUM_RE = re.compile(r"\d{1,3}")
_SCENE_SUFFIXES = ("장면", "모드", "씬")


@dataclass(frozen=True, slots=True)
class Vocab:
    """집 구조에서 만든 어휘. 정적 어휘를 집 어휘가 덮어쓴다."""

    lexicon: dict[str, Lex]
    max_len: int
    terms: dict[str, frozenset[str]] = field(default_factory=dict)


def _merge_ids(table: dict[str, Lex], kind: Kind, surface: str, entity_id: str) -> None:
    existing = table.get(surface)
    if existing is not None and existing.kind is kind:
        ids = (*existing.ids, entity_id) if entity_id not in existing.ids else existing.ids
        table[surface] = Lex(kind, surface, ids=ids)
        return
    table[surface] = Lex(kind, surface, ids=(entity_id,))


def build_vocab(home: HomeModel) -> Vocab:
    """집 구조(방, 엔티티)에서 어휘를 만든다. 엔티티 id 별 이름 조각(terms)도 함께 만든다."""
    table = dict(STATIC_LEXICON)
    home_table: dict[str, Lex] = {}
    area_names = {a.area_id: a.name for a in home.areas}
    for area in home.areas:
        for name in (area.name, *area.aliases):
            surface = compact_form(name)
            if surface:
                _merge_ids(home_table, Kind.AREA, surface, area.area_id)

    terms: dict[str, frozenset[str]] = {}
    for entity in home.entities:
        base = entity_base_name(entity.name, area_names.get(entity.area_id or ""))
        names = {entity.name, base, *entity.aliases}
        terms[entity.entity_id] = frozenset(t for t in map(compact_form, names) if t)
        if entity.domain == "scene":
            for name in (entity.name, *entity.aliases):
                surface = compact_form(name)
                _merge_ids(home_table, Kind.SCENE, surface, entity.entity_id)
                stripped = next(
                    (surface.removesuffix(s) for s in _SCENE_SUFFIXES if surface.endswith(s)), ""
                )
                if stripped and stripped != surface:
                    _merge_ids(home_table, Kind.SCENE, stripped, entity.entity_id)
            continue
        # 방 이름이 붙은 전체 이름("거실 조명")은 등록하지 않는다. 방+기기 단어로 쪼개져야
        # "거실하고 침실 조명"처럼 방이 따로 해석된다.
        own_names = entity.aliases if base != entity.name else (entity.name, *entity.aliases)
        for name in own_names:
            surface = compact_form(name)
            if surface and surface not in STATIC_LEXICON:
                _merge_ids(home_table, Kind.ENTITY, surface, entity.entity_id)
        base_surface = compact_form(base)
        if base_surface and base_surface not in STATIC_LEXICON:
            _merge_ids(home_table, Kind.ENTITY, base_surface, entity.entity_id)

    table.update(home_table)
    return Vocab(lexicon=table, max_len=max(map(len, table), default=1), terms=terms)


def _next_lex(compact: str, pos: int, vocab: Vocab, nominal: bool, after_ask: bool) -> Lex | None:
    pct = _PCT_RE.match(compact, pos)
    if pct is not None:
        return Lex(Kind.PCT, pct.group(), number=int(pct.group()[:-1]))
    num = _NUM_RE.match(compact, pos)
    if num is not None:
        return Lex(Kind.NUM, num.group(), number=int(num.group()))
    for size in range(min(vocab.max_len, len(compact) - pos), 0, -1):
        piece = compact[pos : pos + size]
        found = vocab.lexicon.get(piece)
        if found is not None:
            return found
        if nominal and piece in PARTICLES:
            return Lex(Kind.PARTICLE, piece)
        if after_ask and piece in ASK_ENDS:
            return Lex(Kind.ASK_END, piece)
    return None


def scan(compact: str, vocab: Vocab) -> list[Lex] | None:
    """공백 없는 발화를 어휘로 남김없이 쪼갠다. 실패하면 None."""
    result: list[Lex] = []
    pos = 0
    nominal = False
    after_ask = False
    while pos < len(compact):
        lex = _next_lex(compact, pos, vocab, nominal, after_ask)
        if lex is None:
            return None
        result.append(lex)
        pos += len(lex.surface)
        nominal = lex.kind in _NOMINAL
        after_ask = lex.kind is Kind.ASK
    return result
