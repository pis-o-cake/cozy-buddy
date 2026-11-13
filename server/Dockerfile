# 빌드 컨텍스트는 server/ 이고, 프로토 정의는 compose 의 additional_contexts(protocol)로 받는다.
FROM python:3.12-slim AS proto
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
WORKDIR /build
COPY scripts/gen_proto.sh scripts/gen_proto.sh
COPY --from=protocol . /protocol/proto
ENV PROTO_DIR=/protocol/proto
RUN sh scripts/gen_proto.sh

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY --from=proto /build/gen ./gen

ENV PYTHONPATH=/app/gen:/app \
    PYTHONUNBUFFERED=1 \
    COZY_DATABASE_URL=sqlite+aiosqlite:////app/data/cozy.db

RUN useradd --system --uid 10001 cozy && mkdir -p /app/data && chown cozy /app/data
USER cozy
VOLUME /app/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# WARNING: 워커는 반드시 1개다. WebSocket 연결 레지스트리가 프로세스 메모리에 있어
# 워커가 둘 이상이면 허브와 앱이 서로 다른 프로세스에 붙어 메시지가 전달되지 않는다.
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn 'app.main:create_app()' -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:8000"]
