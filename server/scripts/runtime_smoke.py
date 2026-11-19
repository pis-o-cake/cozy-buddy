"""런타임 의존성만 설치된 환경에서 앱이 import·구성되는지 확인한다.

check.sh 가 main 의존성만 담은 격리 venv 로 실행한다. 개발 의존성에 기대 통과하던
import 누락(예: httpx 가 dev 에만 있는 경우)을 배포 전에 잡는 것이 목적이다.
"""

import importlib
import pkgutil
import sys

import app
from app.config import Settings
from app.main import create_app


def import_all_modules() -> list[str]:
    """app 패키지 아래 모든 모듈을 import 하고 이름 목록을 돌려준다."""
    names = [m.name for m in pkgutil.walk_packages(app.__path__, prefix="app.")]
    for name in names:
        importlib.import_module(name)
    return names


def main() -> int:
    """모듈 전체 import 후 모든 선택 기능을 켠 설정으로 앱을 만든다."""
    names = import_all_modules()
    # 음성·HA·날씨 경로의 클라이언트까지 만들어지도록 선택 기능을 모두 켠다.
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        static_tokens="hub:smoke-hub:smoke-token",  # secret-scan: allow
        ha_url="http://127.0.0.1:1",
        ha_token="smoke-ha-token",
        openai_api_key="smoke-key",
        openai_stt_model="smoke-stt",
        openai_llm_model="smoke-llm",
        openai_tts_model="smoke-tts",
        openai_tts_voice="smoke-voice",
        home_latitude=37.5,
        home_longitude=127.0,
    )
    create_app(settings)
    print(f"runtime smoke ok: {len(names)} modules imported, create_app built")
    return 0


if __name__ == "__main__":
    sys.exit(main())
