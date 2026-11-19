"""인증 REST API. /api/auth.

관리 엔드포인트는 `Authorization: Bearer <COZY_ADMIN_TOKEN>` 이 필요하다.
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.connections import ConnectionManager, get_connections
from app.core.database import get_session
from app.core.exceptions import AuthenticationError
from app.domain.auth.errors import AdminUnconfiguredError
from app.domain.auth.pairing import PairingGrant, PairingStore
from app.domain.auth.schemas import PairingCodeCreate, PairingCodeRead, PairRequest, PairResponse
from app.domain.auth.service import AuthService

router = APIRouter()

_BEARER = "bearer "


def get_pairing_store(request: Request) -> PairingStore:
    """등록 코드 저장소 의존성. 처음 쓸 때 만든다."""
    store: PairingStore | None = getattr(request.app.state, "pairing", None)
    if store is None:
        store = PairingStore()
        request.app.state.pairing = store
    return store


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    store: Annotated[PairingStore, Depends(get_pairing_store)],
    connections: Annotated[ConnectionManager, Depends(get_connections)],
) -> AuthService:
    """AuthService 의존성."""
    return AuthService(session, store, connections)


def require_admin(request: Request) -> None:
    """관리자 토큰을 확인한다. 설정이 없으면 503, 틀리면 401."""
    settings: Settings = request.app.state.settings
    expected = settings.admin_token.get_secret_value()
    if not expected:
        raise AdminUnconfiguredError("COZY_ADMIN_TOKEN is not set")
    header = request.headers.get("authorization", "")
    given = header[len(_BEARER) :] if header.lower().startswith(_BEARER) else ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise AuthenticationError("Invalid admin token")


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
AdminDep = Depends(require_admin)


@router.post("/pairing-codes", dependencies=[AdminDep])
async def create_pairing_code(body: PairingCodeCreate, service: AuthServiceDep) -> PairingCodeRead:
    """6자리 1회용 등록 코드를 발급한다(5분 유효)."""
    return service.issue_code(PairingGrant(name=body.name, area_id=body.area_id))


@router.post("/pair")
async def pair(body: PairRequest, request: Request, service: AuthServiceDep) -> PairResponse:
    """코드로 client_id 와 토큰을 받는다. IP 별 분당 5회로 제한한다."""
    ip = request.client.host if request.client else "unknown"
    return await service.pair(ip, body.code, body.client_kind)


@router.delete("/clients/{client_id}", dependencies=[AdminDep], status_code=204)
async def revoke_client(client_id: str, service: AuthServiceDep) -> Response:
    """클라이언트 토큰을 폐기하고 연결된 세션을 끊는다."""
    await service.revoke(client_id)
    return Response(status_code=204)
