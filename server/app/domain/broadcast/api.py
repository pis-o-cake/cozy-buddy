"""방송 REST API. /api/broadcast. 휴대폰 앱이 허브들에 방송을 보낼 때 쓴다."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import ClientRole, Settings
from app.core.exceptions import AuthenticationError, NotFoundError
from app.domain.agent.home_view import build_home_view
from app.domain.broadcast.schemas import BroadcastBody, BroadcastResult
from app.domain.broadcast.service import BroadcastRequest, BroadcastService

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)
KEY_NO_HUB = "hub.not_found"


def get_broadcast_service(request: Request) -> BroadcastService:
    """BroadcastService 의존성."""
    service: BroadcastService = request.app.state.broadcast
    return service


def _accepted_tokens(settings: Settings) -> list[bytes]:
    """REST 호출을 허용하는 토큰. 설정에 관리자 토큰이 있으면 그것도 허용한다."""
    tokens = [
        entry.token.get_secret_value()
        for entry in settings.token_entries
        if entry.kind == ClientRole.MOBILE
    ]
    # TODO: W3-1 의 COZY_ADMIN_TOKEN 설정이 병합되면 admin_token 이 여기서 자동으로 허용된다.
    admin = getattr(settings, "admin_token", None)
    secret = admin.get_secret_value() if admin is not None else ""
    if secret:
        tokens.append(secret)
    return [token.encode() for token in tokens]


def require_caller(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Bearer 토큰이 모바일 정적 토큰이나 관리자 토큰과 같을 때만 통과시킨다.

    Raises:
        AuthenticationError: 토큰이 없거나 일치하는 것이 없을 때. 허용 토큰이 없어도 막는다.
    """
    settings: Settings = request.app.state.settings
    if credentials is None:
        raise AuthenticationError("Missing bearer token")
    candidate = credentials.credentials.encode()
    matched = False
    # IMPORTANT: 조기 반환하지 않고 전체를 순회해 일치 위치가 타이밍으로 새지 않게 한다.
    for token in _accepted_tokens(settings):
        matched = hmac.compare_digest(candidate, token) or matched
    if not matched:
        raise AuthenticationError("Invalid bearer token")


@router.post("", dependencies=[Depends(require_caller)])
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
