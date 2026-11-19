"""요청 하나의 문맥. 이해·실행 단계가 함께 쓴다."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.agent.home_view import HomeView


@dataclass(frozen=True, slots=True)
class TurnContext:
    """발화 한 건을 처리하는 동안의 문맥. 집 상태는 요청 시작 시점의 사본이다.

    Attributes:
        view: 요청 시작 시점의 집 구조와 상태.
        hub_id: 말한 허브. 모르면 빈 문자열.
        area_id: 말한 허브가 놓인 방. 모르면 빈 문자열.
        now: 시간대가 있는 현재 시각.
    """

    view: HomeView
    hub_id: str
    area_id: str
    now: datetime
