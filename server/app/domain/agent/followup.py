"""확인 질문에 대한 짧은 후속 답("응", "아니", "거실")을 해석한다."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.agent.model import EntityRef, HomeModel
from app.domain.agent.normalize import compact_form, entity_base_name

_YES = frozenset(
    {"응", "응응", "네", "네네", "예", "넵", "어", "그래", "그래요", "좋아", "좋아요", "맞아",
     "맞아요", "그렇게해", "진행해", "해줘", "해", "오케이", "ok"}
)  # fmt: skip
_NO = frozenset(
    {"아니", "아니요", "아니야", "아니에요", "아뇨", "싫어", "싫어요", "취소", "하지마", "됐어",
     "됐어요", "그만", "안해"}
)  # fmt: skip
_ALL = frozenset({"둘다", "모두", "전부", "다", "다해줘", "전부다", "모두다"})
_MIN_NAME_LEN = 2


class Answer(StrEnum):
    """후속 답의 종류."""

    YES = "yes"
    NO = "no"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class Narrowed:
    """후보를 좁힌 결과. changed 가 False 면 발화가 후보를 가르지 못한 것이다."""

    entities: tuple[EntityRef, ...]
    changed: bool


def classify_answer(text: str) -> Answer | None:
    """예/아니오/전부 응답이면 종류를, 아니면 None."""
    compact = compact_form(text)
    if compact in _YES:
        return Answer.YES
    if compact in _NO:
        return Answer.NO
    if compact in _ALL:
        return Answer.ALL
    return None


def narrow_candidates(text: str, candidates: list[EntityRef], home: HomeModel) -> Narrowed:
    """발화에 나온 방 이름·기기 이름으로 후보를 좁힌다."""
    compact = compact_form(text)
    hit_areas = {
        area.area_id
        for area in home.areas
        if any(compact_form(n) and compact_form(n) in compact for n in (area.name, *area.aliases))
    }
    pool = [e for e in candidates if e.area_id in hit_areas] if hit_areas else list(candidates)
    area_names = {a.area_id: a.name for a in home.areas}
    named = [
        e
        for e in pool
        if len(base := compact_form(entity_base_name(e.name, area_names.get(e.area_id or ""))))
        >= _MIN_NAME_LEN
        and base in compact
    ]
    if named:
        pool = named
    return Narrowed(tuple(pool), changed=0 < len(pool) < len(candidates))
