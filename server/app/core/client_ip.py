"""요청의 실제 클라이언트 IP 해석. 신뢰 프록시가 보낸 헤더만 믿는다."""

import ipaddress
from functools import lru_cache

from starlette.requests import HTTPConnection

UNKNOWN_IP = "unknown"

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def parse_trusted_proxies(raw: str) -> tuple[Network, ...]:
    """쉼표로 구분한 IP/CIDR 목록을 파싱한다.

    Args:
        raw: `127.0.0.1,172.16.0.0/12` 형태. 빈 항목은 무시한다.

    Returns:
        네트워크 튜플.

    Raises:
        ValueError: IP/CIDR 형식이 틀렸을 때.
    """
    items = (item.strip() for item in raw.split(","))
    return tuple(ipaddress.ip_network(item, strict=False) for item in items if item)


def _parse_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def resolve_client_ip(request: HTTPConnection, trusted_proxies: str) -> str:
    """클라이언트 IP 를 돌려준다.

    직접 연결한 peer 가 신뢰 프록시일 때만 `X-Real-IP`, 없으면 `X-Forwarded-For` 의
    마지막 hop(프록시가 덧붙인 값)을 쓴다. 그 외에는 peer 주소를 쓴다.

    Args:
        request: 현재 요청.
        trusted_proxies: 신뢰 프록시 IP/CIDR 쉼표 목록.

    Returns:
        클라이언트 IP 문자열. peer 를 알 수 없으면 `unknown`.
    """
    peer = request.client.host if request.client else None
    if peer is None:
        return UNKNOWN_IP
    peer_ip = _parse_ip(peer)
    if peer_ip is None:
        return peer
    address = ipaddress.ip_address(peer_ip)
    networks = parse_trusted_proxies(trusted_proxies)
    if not any(net.version == address.version and address in net for net in networks):
        return peer

    real_ip = request.headers.get("x-real-ip")
    if real_ip and (parsed := _parse_ip(real_ip)):
        return parsed
    # IMPORTANT: 맨 앞 hop 은 클라이언트가 위조할 수 있다. 프록시가 덧붙인 마지막 값을 쓴다.
    forwarded = request.headers.get("x-forwarded-for", "")
    last = forwarded.rsplit(",", 1)[-1] if forwarded else ""
    return _parse_ip(last) or peer
