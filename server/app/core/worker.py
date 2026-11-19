"""Gunicorn 워커. uvicorn 워커에 WebSocket 메시지 크기 상한을 건다."""

from uvicorn_worker import UvicornWorker

# 허브 오디오 조각은 20ms PCM(640 B) 단위라 1 MiB 면 충분하다. uvicorn 기본값은 16 MiB 다.
WS_MAX_MESSAGE_BYTES = 1024 * 1024


class CozyUvicornWorker(UvicornWorker):  # type: ignore[misc]
    """`ws_max_size` 를 줄인 UvicornWorker."""

    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, "ws_max_size": WS_MAX_MESSAGE_BYTES}
