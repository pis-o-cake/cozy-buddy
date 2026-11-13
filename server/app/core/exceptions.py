"""서버 공통 예외와 FastAPI 핸들러."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.i18n import Translator


class CozyError(Exception):
    """서버 예외의 기반 클래스.

    메시지는 내부용(영어)이고, 사용자에게는 message_key 를 언어팩으로 풀어 내려준다.
    """

    status_code = 500
    default_key = "error.internal"

    def __init__(self, message: str, message_key: str | None = None) -> None:
        super().__init__(message)
        self.message_key = message_key or self.default_key


class BadRequestError(CozyError):
    """잘못된 요청."""

    status_code = 400
    default_key = "error.bad_request"


class AuthenticationError(CozyError):
    """인증 실패."""

    status_code = 401
    default_key = "error.unauthenticated"


class NotFoundError(CozyError):
    """대상 없음."""

    status_code = 404
    default_key = "error.not_found"


def register_exception_handlers(app: FastAPI) -> None:
    """CozyError 를 i18n 키가 담긴 JSON 응답으로 변환하는 핸들러를 등록한다."""

    async def handle(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, CozyError):
            raise exc
        translator: Translator = request.app.state.translator
        logger.warning("Request failed: {}", exc)
        return JSONResponse(
            status_code=exc.status_code,
            content={"message_key": exc.message_key, "message": translator(exc.message_key)},
        )

    app.add_exception_handler(CozyError, handle)
