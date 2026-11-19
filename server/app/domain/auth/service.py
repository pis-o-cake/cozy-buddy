"""등록·토큰 검증·폐기 비즈니스 로직."""

import hashlib
import hmac
import secrets
import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ClientRole
from app.core.connections import ConnectionManager
from app.core.database import utcnow
from app.core.exceptions import NotFoundError
from app.core.security import hash_token
from app.domain.auth import crud
from app.domain.auth.errors import PairingCodeInvalidError, RateLimitedError
from app.domain.auth.models import ClientToken
from app.domain.auth.pairing import PAIRING_CODE_TTL_SECONDS, PairingGrant, PairingStore
from app.domain.auth.schemas import PairingCodeRead, PairResponse
from app.domain.hub import crud as hub_crud
from app.domain.hub.models import Hub

TOKEN_BYTES = 32
CLOSE_REVOKED = 1008
_DUMMY_HASH = hashlib.sha256(b"dummy").hexdigest()


class AuthService:
    """페어링으로 발급한 클라이언트 토큰을 관리한다."""

    def __init__(
        self, session: AsyncSession, store: PairingStore, connections: ConnectionManager
    ) -> None:
        self._session = session
        self._store = store
        self._connections = connections

    def issue_code(self, grant: PairingGrant) -> PairingCodeRead:
        """등록 코드를 발급한다."""
        code = self._store.issue(grant)
        logger.info("Pairing code issued: name={}", grant.name)
        return PairingCodeRead(code=code, expires_in_seconds=PAIRING_CODE_TTL_SECONDS)

    async def pair(self, ip: str, code: str, kind: ClientRole) -> PairResponse:
        """코드를 소비하고 client_id 와 토큰을 발급한다.

        Raises:
            RateLimitedError: IP 별 시도 한도를 넘었을 때.
            PairingCodeInvalidError: 코드가 없거나 만료됐거나 이미 쓰였을 때.
        """
        if not self._store.allow_attempt(ip):
            logger.warning("Pair attempts rate limited: ip={}", ip)
            raise RateLimitedError("Too many pairing attempts")
        grant = self._store.consume(code)
        if grant is None:
            logger.warning("Pair rejected: invalid or expired code, ip={}", ip)
            raise PairingCodeInvalidError("Invalid or expired pairing code")

        client_id = f"{kind.value}-{secrets.token_hex(4)}"
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await crud.add(
            self._session,
            ClientToken(
                token_id=uuid.uuid4().hex,
                client_id=client_id,
                kind=kind.value,
                token_hash=hash_token(token),
            ),
        )
        if kind == ClientRole.HUB:
            await hub_crud.add(
                self._session,
                Hub(hub_id=client_id, name=grant.name, area_id=grant.area_id),
            )
        logger.info("Client paired: {} {}", kind, client_id)
        return PairResponse(client_id=client_id, token=token)

    async def verify(self, kind: ClientRole, client_id: str, token: str) -> bool:
        """발급 토큰과 일치하는지 해시 비교(상수 시간)로 확인한다."""
        candidate = hash_token(token).encode()
        rows = await crud.list_active(self._session, client_id)
        matched = False
        # IMPORTANT: 조기 반환 없이, 행이 없어도 더미 비교를 해 존재 여부가 새지 않게 한다.
        hashes = [(row.token_hash, row.kind) for row in rows] or [(_DUMMY_HASH, "")]
        for stored, stored_kind in hashes:
            if hmac.compare_digest(candidate, stored.encode()) and stored_kind == kind.value:
                matched = True
        return matched

    async def revoke(self, client_id: str) -> None:
        """토큰을 폐기하고 연결된 세션을 끊는다.

        Raises:
            NotFoundError: 폐기할 활성 토큰이 없을 때.
        """
        count = await crud.revoke_client(self._session, client_id, utcnow())
        if count == 0:
            raise NotFoundError(f"No active token for client: {client_id}", "auth.client_not_found")
        await self._connections.disconnect_client(client_id, CLOSE_REVOKED)
        logger.info("Client revoked: {} tokens={}", client_id, count)
