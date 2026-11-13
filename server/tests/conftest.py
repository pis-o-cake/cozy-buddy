"""테스트 공용 픽스처. 테스트마다 임시 SQLite 파일에 실제 Alembic 마이그레이션을 적용한다."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import HomeSnapshot
from cozy.v1.session_pb2 import CLIENT_KIND_HUB, CLIENT_KIND_MOBILE, ClientHello
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.domain.home.service import HomeService
from app.main import create_app
from tests.fake_ha import TOKEN as FAKE_HA_TOKEN
from tests.fake_ha import FakeHomeAssistant

SERVER_DIR = Path(__file__).resolve().parent.parent
HUB_ID = "hub-1"
HUB_TOKEN = "hub-secret"
MOBILE_ID = "phone-1"
MOBILE_TOKEN = "mobile-secret"


def migrate(url: str) -> None:
    """임시 DB 를 최신 스키마로 올린다."""
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        static_tokens=(
            f"hub:{HUB_ID}:{HUB_TOKEN},hub:hub-2:hub2-secret,mobile:{MOBILE_ID}:{MOBILE_TOKEN}"
        ),
        hello_timeout_seconds=0.5,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    migrate(settings.database_url)
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    fake.start()
    yield fake
    fake.close()


@pytest.fixture
def ha_settings(settings: Settings, fake_ha: FakeHomeAssistant) -> Settings:
    return settings.model_copy(
        update={
            "ha_url": fake_ha.url,
            "ha_token": SecretStr(FAKE_HA_TOKEN),
            "ha_request_timeout_seconds": 0.5,
            "ha_backoff_min_seconds": 0.05,
            "ha_backoff_max_seconds": 0.1,
            "ha_registry_debounce_seconds": 0.1,
            "ha_expose_poll_seconds": 0.0,
        }
    )


@pytest.fixture
def ha_client(ha_settings: Settings) -> Iterator[TestClient]:
    """가짜 HA 에 연결된 앱. 캐시가 만들어질 때까지 기다렸다 돌려준다."""
    migrate(ha_settings.database_url)
    with TestClient(create_app(ha_settings)) as c:
        home: HomeService = c.app.state.home  # type: ignore[attr-defined]
        eventually(lambda: home.ready)
        yield c


def hello_envelope(
    kind: int = CLIENT_KIND_HUB,
    client_id: str = HUB_ID,
    token: str = HUB_TOKEN,
    version: int = 1,
) -> Envelope:
    return Envelope(
        client_hello=ClientHello(
            kind=kind, client_id=client_id, token=token, protocol_version=version
        )
    )


def send(ws: WebSocketTestSession, envelope: Envelope) -> None:
    ws.send_bytes(envelope.SerializeToString())


def recv(ws: WebSocketTestSession) -> Envelope:
    envelope = Envelope()
    envelope.ParseFromString(ws.receive_bytes())
    return envelope


def connect_hub(
    client: TestClient,
    hub_id: str = HUB_ID,
    token: str = HUB_TOKEN,
    consume_sync_request: bool = True,
) -> "_Connected":
    """허브로 hello 까지 마친 WebSocket 컨텍스트를 연다.

    ServerHello 와 HomeSnapshot 을 소비하고, 기본값으로 뒤따르는 AlarmSyncRequest 도 소비한다.
    소비한 스냅샷은 컨텍스트 객체의 `home_snapshot` 으로 볼 수 있다.
    """
    return _Connected(client, CLIENT_KIND_HUB, hub_id, token, consume_sync_request)


def connect_mobile(client: TestClient) -> "_Connected":
    """앱으로 hello 까지 마친 WebSocket 컨텍스트를 연다."""
    return _Connected(client, CLIENT_KIND_MOBILE, MOBILE_ID, MOBILE_TOKEN)


class _Connected:
    def __init__(
        self, client: TestClient, kind: int, client_id: str, token: str, consume_sync: bool = False
    ) -> None:
        self._consume_sync = consume_sync
        self._cm = client.websocket_connect("/ws")
        self._hello = hello_envelope(kind, client_id, token)
        self.server_hello: Envelope | None = None
        self.home_snapshot: HomeSnapshot | None = None

    def __enter__(self) -> WebSocketTestSession:
        ws = self._cm.__enter__()
        send(ws, self._hello)
        self.server_hello = recv(ws)
        snapshot = recv(ws)
        assert snapshot.WhichOneof("body") == "home_snapshot"
        self.home_snapshot = snapshot.home_snapshot
        if self._consume_sync:
            assert recv(ws).WhichOneof("body") == "alarm_sync_request"
        return ws

    def __exit__(self, *exc: object) -> None:
        self._cm.__exit__(*exc)  # type: ignore[arg-type]


def eventually[T](check: Callable[[], T | None], timeout: float = 3.0) -> T:
    """비동기로 반영되는 서버 상태를 폴링한다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Condition not met in time")
