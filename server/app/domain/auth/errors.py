"""인증 도메인 예외."""

from app.core.exceptions import CozyError


class AdminUnconfiguredError(CozyError):
    """관리자 토큰이 설정되지 않아 관리 API 를 쓸 수 없다."""

    status_code = 503
    default_key = "auth.admin_unconfigured"


class RateLimitedError(CozyError):
    """시도 횟수 제한을 넘었다."""

    status_code = 429
    default_key = "auth.rate_limited"


class PairingCodeInvalidError(CozyError):
    """등록 코드가 없거나 만료됐거나 이미 쓰였다."""

    status_code = 400
    default_key = "auth.pairing_code_invalid"
