"""Model ids, audio constants and secret loading (key is never logged)."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from dotenv import load_dotenv

ENV_PATH = "/Users/cheol/dev/private/cozy-buddy/.env"
SAMPLE_RATE = 24000
INPUT_VOICE = "echo"
RESPONSE_VOICE = "coral"


def load_env() -> None:
    """Load OPENAI_API_KEY from the main-checkout .env without overriding the environment."""
    load_dotenv(ENV_PATH, override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")


@dataclass
class Models:
    stt: str = field(default_factory=lambda: os.environ.get("S2_STT_MODEL", "gpt-4o-transcribe"))
    llm: str = field(default_factory=lambda: os.environ.get("S2_LLM_MODEL", "gpt-5.4-nano"))
    tts: str = field(default_factory=lambda: os.environ.get("S2_TTS_MODEL", "gpt-4o-mini-tts"))
    realtime: str = field(
        default_factory=lambda: os.environ.get("S2_REALTIME_MODEL", "gpt-realtime-2.1-mini")
    )

    def as_dict(self) -> dict[str, str]:
        return asdict(self)
