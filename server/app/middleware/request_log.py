"""HTTP 요청 로깅 미들웨어."""

import re
import time
import uuid

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")


def sanitize_request_id(raw: str | None) -> str:
    """클라이언트가 보낸 요청 ID 가 안전하면 그대로, 아니면 새로 만든다.

    로그 위조(개행 삽입 등)를 막으려고 64자 이하 `[A-Za-z0-9._-]` 만 받는다.
    """
    if raw is not None and _REQUEST_ID_PATTERN.fullmatch(raw):
        return raw
    return uuid.uuid4().hex


class RequestLogMiddleware(BaseHTTPMiddleware):
    """요청마다 X-Request-ID 를 붙이고 처리 시간을 기록한다. WebSocket 은 대상이 아니다."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = sanitize_request_id(request.headers.get("x-request-id"))
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        logger.info(
            "{} {} -> {} ({:.1f} ms) rid={}",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response
