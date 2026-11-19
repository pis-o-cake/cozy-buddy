"""방송 REST API. /api/broadcast. 휴대폰 앱이 허브들에 방송을 보낼 때 쓴다."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.exceptions import NotFoundError
from app.core.rest_auth import require_admin_or_mobile
from app.domain.agent.home_view import build_home_view
from app.domain.broadcast.schemas import BroadcastBody, BroadcastResult
from app.domain.broadcast.service import BroadcastRequest, BroadcastService

router = APIRouter()
KEY_NO_HUB = "hub.not_found"


def get_broadcast_service(request: Request) -> BroadcastService:
    """BroadcastService 의존성."""
    service: BroadcastService = request.app.state.broadcast
    return service


@router.post("", dependencies=[Depends(require_admin_or_mobile)])
async def broadcast(
    body: BroadcastBody,
    request: Request,
    service: Annotated[BroadcastService, Depends(get_broadcast_service)],
) -> BroadcastResult:
    """대상 허브들에 방송한다. 대상 허브를 하나도 찾지 못하면 404."""
    areas = build_home_view(request.app.state.home.snapshot()).model.areas
    report = await service.send(
        BroadcastRequest(
            target=body.target, text=body.text, from_hub_id=body.from_hub_id, areas=areas
        )
    )
    if report.matched == 0:
        raise NotFoundError(f"No hub matches broadcast target: {body.target}", KEY_NO_HUB)
    return BroadcastResult(
        target=report.label,
        delivered=list(report.delivered),
        offline=list(report.offline),
        audio=report.audio,
    )
