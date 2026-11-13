"""Input audio cache: TTS synthesis (synthetic) and microphone recording (real)."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from loguru import logger
from openai import AsyncOpenAI

from s2.config import INPUT_VOICE, SAMPLE_RATE

ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = ROOT / "audio"
COMMANDS_FILE = ROOT / "commands.jsonl"


def load_commands(limit: int | None = None) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in COMMANDS_FILE.read_text(encoding="utf-8").splitlines() if line]
    return rows[:limit] if limit else rows


def audio_path(cmd_id: str) -> Path:
    return AUDIO_DIR / f"{cmd_id}.pcm"


def read_pcm(cmd_id: str) -> bytes:
    return audio_path(cmd_id).read_bytes()


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.frombuffer(pcm, dtype="<i2"), SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def duration_s(pcm: bytes) -> float:
    return len(pcm) / 2 / SAMPLE_RATE


async def synthesize_all(client: AsyncOpenAI, tts_model: str, commands: list[dict[str, Any]]) -> int:
    """Synthesize missing command audio; returns the number of newly created files."""
    AUDIO_DIR.mkdir(exist_ok=True)
    created = 0
    for cmd in commands:
        path = audio_path(cmd["id"])
        if path.exists():
            continue
        async with client.audio.speech.with_streaming_response.create(
            model=tts_model, voice=INPUT_VOICE, input=cmd["text"], response_format="pcm"
        ) as resp:
            pcm = await resp.read()
        path.write_bytes(pcm)
        created += 1
        logger.info("Synthesized {} ({:.1f}s)", cmd["id"], duration_s(pcm))
    return created


def record_command(cmd_id: str, seconds: float) -> None:
    """Record one command from the default microphone into the shared cache (real voice)."""
    import sounddevice as sd

    logger.info("Recording {} for {:.1f}s", cmd_id, seconds)
    data = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    AUDIO_DIR.mkdir(exist_ok=True)
    audio_path(cmd_id).write_bytes(data.tobytes())
