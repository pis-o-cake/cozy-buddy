"""방송 REST API. /api/broadcast. 휴대폰 앱이 허브들에 방송을 보낼 때 쓴다."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.exceptions import NotFoundError
from app.core.rest_auth import Principal, Role, require_admin_or_mobile
from app.core.usage_guard import BucketRegistry
from app.domain.agent.home_view import build_home_view
from app.domain.auth.errors import RateLimitedError
from app.domain.broadcast.schemas import BroadcastBody, BroadcastResult
from app.domain.broadcast.service import BroadcastRequest, BroadcastService

router = APIRouter()
KEY_NO_HUB = "hub.not_found"
KEY_RATE_LIMITED = "auth.rate_limited"


def get_broadcast_service(request: Request) -> BroadcastService:
    """BroadcastService 의존성."""
    service: BroadcastService = request.app.state.broadcast
    return service


def _limiter(request: Request) -> BucketRegistry:
    """호출자별 분당 방송 한도. 첫 호출 때 만들어 앱 상태에 둔다."""
    state = request.app.state
    limiter: BucketRegistry | None = getattr(state, "broadcast_limiter", None)
    if limiter is None:
        limiter = state.broadcast_limiter = BucketRegistry(state.settings.broadcast_rate_per_minute)
    return limiter


@router.post("")
async def broadcast(
    body: BroadcastBody,
    request: Request,
    service: Annotated[BroadcastService, Depends(get_broadcast_service)],
    principal: Annotated[Principal, Depends(require_admin_or_mobile)],
) -> BroadcastResult:
    """대상 허브들에 방송한다. 대상 허브를 하나도 찾지 못하면 404, 한도를 넘으면 429.

    IMPORTANT: `from_hub_id` 는 관리자만 쓸 수 있다. 앱이 보내면 무시해 다른 허브 행세를 막는다.
    """
    if not _limiter(request).try_take(principal.client_id):
        raise RateLimitedError("Broadcast rate limit exceeded", KEY_RATE_LIMITED)
    from_hub_id = body.from_hub_id if principal.role == Role.ADMIN else ""
    areas = build_home_view(request.app.state.home.snapshot()).model.areas
    report = await service.send(
        BroadcastRequest(target=body.target, text=body.text, from_hub_id=from_hub_id, areas=areas)
    )
    if report.matched == 0:
        raise NotFoundError(f"No hub matches broadcast target: {body.target}", KEY_NO_HUB)
    return BroadcastResult(
        target=report.label,
        delivered=list(report.delivered),
        offline=list(report.offline),
        audio=report.audio,
    )
