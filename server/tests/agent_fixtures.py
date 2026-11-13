"""에이전트 매처 테스트 공용 픽스처 데이터."""

from datetime import datetime, timedelta, timezone

from app.domain.agent.model import AreaRef, EntityRef, HomeModel

KST = timezone(timedelta(hours=9))
# 2026-11-13 은 금요일이다. 15시 30분이라 "7시" 같은 모호한 시각은 다음 오전 7시로 풀린다.
NOW = datetime(2026, 11, 13, 15, 30, tzinfo=KST)

LIVING = "living"
BEDROOM = "bedroom"
KITCHEN = "kitchen"
KIDS = "kids"

HOME = HomeModel(
    areas=[
        AreaRef(LIVING, "거실"),
        AreaRef(BEDROOM, "침실", aliases=("안방",)),
        AreaRef(KITCHEN, "주방"),
        AreaRef(KIDS, "아이방"),
    ],
    entities=[
        EntityRef("light.living_ceiling", LIVING, "거실 조명", "light"),
        EntityRef("light.living_stand", LIVING, "스탠드", "light"),
        EntityRef("light.bedroom_ceiling", BEDROOM, "침실 조명", "light"),
        EntityRef("light.bedroom_stand", BEDROOM, "스탠드", "light"),
        EntityRef("light.kitchen_ceiling", KITCHEN, "주방 조명", "light"),
        EntityRef("switch.kitchen_kettle", KITCHEN, "주방 전기포트", "switch"),
        EntityRef("fan.living_fan", LIVING, "거실 선풍기", "fan"),
        EntityRef("cover.bedroom_curtain", BEDROOM, "침실 커튼", "cover"),
        EntityRef("climate.kids_ac", KIDS, "아이방 에어컨", "climate"),
        EntityRef("sensor.living_temp", LIVING, "거실 온도", "sensor"),
        EntityRef("scene.goodnight", None, "굿나잇", "scene"),
        EntityRef("scene.away", None, "외출", "scene"),
    ],
)
