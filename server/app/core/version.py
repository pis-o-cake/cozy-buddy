"""서버 버전 조회."""

import tomllib
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "cozy-server"


def get_server_version() -> str:
    """패키지 메타데이터에서 버전을 읽는다.

    Poetry package-mode=false 라 설치되지 않는 것이 기본이므로 pyproject.toml 로 폴백한다.
    """
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        pass
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():
        return "0.0.0"
    with pyproject.open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])
