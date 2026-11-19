"""REST 기본 거부 인증. 모든 도메인 라우터에 의존성으로 붙고, 라우트별로 역할을 좁힌다."""

import hmac
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.config import Settings
from app.core.database import Database
from app.core.exceptions import AuthenticationError, ForbiddenError
from app.core.security import hash_token
from app.domain.auth.models import ClientToken

ADMIN_CLIENT_ID = "admin"
_bearer = HTTPBearer(auto_error=False)


class Role(StrEnum):
    """REST 호출자의 권한 등급."""

    ADMIN = "admin"
    HUB = "hub"
    MOBILE = "mobile"


@dataclass(frozen=True)
class Principal:
    """인증된 호출자."""

    role: Role
    client_id: str


def _match_static(settings: Settings, candidate: bytes) -> Principal | None:
    """관리자 토큰과 정적 토큰 전체를 상수 시간으로 비교한다."""
    found: Principal | None = None
    # IMPORTANT: 조기 반환하지 않고 전체를 순회해 일치 위치가 타이밍으로 새지 않게 한다.
    admin = settings.admin_token.get_secret_value().encode()
    if admin and hmac.compare_digest(candidate, admin):
        found = Principal(Role.ADMIN, ADMIN_CLIENT_ID)
    for entry in settings.token_entries:
        if hmac.compare_digest(candidate, entry.token.get_secret_value().encode()):
            found = found or Principal(Role(entry.kind.value), entry.client_id)
    return found


async def _match_issued(database: Database, token: str) -> Principal | None:
    """페어링으로 발급한 토큰을 해시로 찾는다. 폐기된 토큰은 제외한다."""
    async with database.session() as session:
        row = await session.scalar(
            select(ClientToken).where(
                ClientToken.token_hash == hash_token(token), ClientToken.revoked_at.is_(None)
            )
        )
    if row is None:
        return None
    return Principal(Role(row.kind), row.client_id)


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Bearer 토큰으로 호출자를 확인한다.

    Raises:
        AuthenticationError: 토큰이 없거나 어느 쪽과도 일치하지 않을 때.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token")
    token = credentials.credentials
    principal = _match_static(request.app.state.settings, token.encode())
    if principal is None:
        principal = await _match_issued(request.app.state.db, token)
    if principal is None:
        raise AuthenticationError("Invalid bearer token")
    return principal


def require_roles(*roles: Role) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """주어진 역할만 통과시키는 의존성을 만든다.

    Raises:
        ForbiddenError: 인증은 됐지만 역할이 허용 목록에 없을 때(의존성 호출 시).
    """
    allowed = frozenset(roles)

    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in allowed:
            raise ForbiddenError(f"Role not permitted: {principal.role}")
        return principal

    return dependency


require_admin_only = require_roles(Role.ADMIN)
require_admin_or_mobile = require_roles(Role.ADMIN, Role.MOBILE)
