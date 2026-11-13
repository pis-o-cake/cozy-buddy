"""정적 토큰 인증."""

import hmac
from collections.abc import Iterable

from app.config import ClientRole, TokenEntry


def authenticate(
    entries: Iterable[TokenEntry], kind: ClientRole, client_id: str, token: str
) -> bool:
    """kind·client_id·token 이 모두 일치하는 항목이 있는지 상수 시간으로 확인한다.

    Args:
        entries: 설정의 정적 토큰 목록.
        kind: 클라이언트가 밝힌 종류.
        client_id: 클라이언트가 밝힌 ID.
        token: 클라이언트가 보낸 토큰.

    Returns:
        인증 성공 여부.
    """
    candidate = token.encode()
    matched = False
    # IMPORTANT: 조기 반환하지 않고 전체를 순회해 일치 위치가 타이밍으로 새지 않게 한다.
    for entry in entries:
        token_ok = hmac.compare_digest(candidate, entry.token.get_secret_value().encode())
        id_ok = hmac.compare_digest(client_id.encode(), entry.client_id.encode())
        if token_ok and id_ok and entry.kind == kind:
            matched = True
    return matched
