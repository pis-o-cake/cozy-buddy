"""cozy.v1 Envelope 헬퍼."""

from cozy.v1.common_pb2 import ErrorCode
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.session_pb2 import ErrorMessage

PROTOCOL_VERSION = 1


def error_envelope(
    code: ErrorCode, message_key: str, request_id: str = "", stream_id: str = ""
) -> Envelope:
    """ErrorMessage 를 담은 Envelope 를 만든다. message_key 는 문구가 아닌 i18n 키다."""
    return Envelope(
        error=ErrorMessage(
            code=code, message_key=message_key, request_id=request_id, stream_id=stream_id
        )
    )
