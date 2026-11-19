"""Synthesize the Korean wake word dataset with OpenAI TTS.

Usage (from spikes/s1-wakeword):
    uv run python -m s1.ko.synth sentences   # negative sentences via a chat model
    uv run python -m s1.ko.synth all          # positives, confusables, sentence negatives

Output: .cache/ko/{pos,neg}/*.wav (16 kHz mono int16) and .cache/ko/manifest.jsonl.
The API key is loaded at runtime from the repo .env and never printed.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from loguru import logger
from scipy.signal import resample_poly

from s1.ko import plan

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = Path(os.environ.get("COZY_ENV_FILE") or ROOT.parents[1] / ".env")
OUT = ROOT / ".cache/ko"
SENTENCES = OUT / "sentences.json"
MANIFEST = OUT / "manifest.jsonl"
BUDGET_USD = 2.0
MINI_USD_PER_AUDIO_MIN = 0.015
TTS1_USD_PER_CHAR = 15.0 / 1e6
TOPICS = [
    "집안 일상 대화", "요리와 식사", "날씨와 계절", "가족과 아이들", "뉴스와 시사", "스포츠 중계 멘트",
    "드라마 대사", "쇼핑과 가격", "여행 계획", "건강과 병원", "직장과 회의", "스마트홈 기기 제어 명령",
]
lock = threading.Lock()
spent = {"usd": 0.0}


def client():
    load_dotenv(ENV_PATH, override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI

    return OpenAI()


def cmd_sentences() -> None:
    api = client()
    sentences: list[str] = []
    for topic in TOPICS:
        resp = api.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{
                "role": "user",
                "content": f'주제 "{topic}" 에 관한 자연스러운 한국어 구어체 문장 36개를 JSON {{"sentences": [...]}} 으로. '
                "길이 6~25어절로 다양하게. '자비스' 라는 단어는 쓰지 말 것.",
            }],
        )
        sentences += json.loads(resp.choices[0].message.content)["sentences"]
    sentences = [s for s in dict.fromkeys(sentences) if "자비스" not in s]
    OUT.mkdir(parents=True, exist_ok=True)
    SENTENCES.write_text(json.dumps(sentences, ensure_ascii=False, indent=1))
    logger.info("sentences: {}", len(sentences))


def write_wav(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(plan.SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def tts(api, spec: dict) -> dict | None:
    key = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    name = hashlib.sha256(key.encode()).hexdigest()[:16]
    rel = f"{spec['kind']}/{name}.wav"
    path = OUT / rel
    if not path.exists():
        with lock:
            if spent["usd"] > BUDGET_USD:
                raise RuntimeError("budget exceeded")
        kwargs = {"model": spec["model"], "voice": spec["voice"], "input": spec["text"], "response_format": "pcm"}
        if spec["model"] == "gpt-4o-mini-tts":
            kwargs["instructions"] = "Speak in Korean. " + plan.INSTRUCTIONS[spec["instr"]]
        else:
            kwargs["speed"] = spec["speed"]
        try:
            pcm24 = np.frombuffer(api.audio.speech.create(**kwargs).content, dtype="<i2").astype(np.float32)
        except Exception as exc:  # noqa: BLE001 - report and continue with the remaining clips
            logger.warning("tts failed {} {}: {}", spec["model"], spec["voice"], type(exc).__name__)
            return None
        pcm = np.clip(resample_poly(pcm24, 2, 3), -32768, 32767).astype(np.int16)
        write_wav(path, pcm)
        minutes = pcm24.shape[0] / 24_000 / 60
        cost = minutes * MINI_USD_PER_AUDIO_MIN if spec["model"] == "gpt-4o-mini-tts" else len(spec["text"]) * TTS1_USD_PER_CHAR
        with lock:
            spent["usd"] += cost + len(spec["text"]) * 0.6e-6
    return {**spec, "file": rel}


def build_specs() -> list[dict]:
    rng = random.Random(7)
    specs: list[dict] = []

    def add(kind, label, text, model, voice, split, instr=None, speed=None):
        specs.append(dict(kind=kind, label=label, text=text, model=model, voice=voice, split=split, instr=instr, speed=speed))

    for voice in plan.MINI_VOICES:
        held = voice in plan.HELDOUT_MINI_VOICES
        instrs = plan.HELDOUT_INSTR if held else plan.TRAIN_INSTR
        split = "heldout" if held else "train"
        for text in plan.POSITIVE_TEXTS:
            for instr in instrs:
                add("pos", "wake", text, "gpt-4o-mini-tts", voice, split, instr=instr)
        for text in plan.COMMAND_TEXTS:
            for instr in instrs[: 2 if held else 4]:
                add("pos", "wake_cmd", text, "gpt-4o-mini-tts", voice, split, instr=instr)
    for voice in plan.TTS1_VOICES:
        held = voice in plan.HELDOUT_TTS1_VOICES
        speeds = plan.TTS1_SPEEDS_HELDOUT if held else plan.TTS1_SPEEDS_TRAIN
        for text in plan.POSITIVE_TEXTS:
            for speed in speeds:
                add("pos", "wake", text, "tts-1", voice, "heldout" if held else "train", speed=speed)

    for words, voices, split in (
        (plan.CONFUSABLES_TRAIN, plan.CONFUSABLE_VOICES_TRAIN, "train"),
        (plan.CONFUSABLES_HELDOUT, plan.CONFUSABLE_VOICES_HELDOUT, "heldout"),
    ):
        instrs = plan.TRAIN_INSTR if split == "train" else plan.HELDOUT_INSTR
        for word in words:
            for voice in voices:
                add("neg", "confusable", word, "gpt-4o-mini-tts", voice, split, instr=rng.choice(instrs))
    for word in plan.CONFUSABLES_TRAIN[:8]:
        for voice in plan.CONFUSABLE_VOICES_HELDOUT[:2]:
            add("neg", "confusable_hv", word, "gpt-4o-mini-tts", voice, "heldout", instr=rng.choice(plan.HELDOUT_INSTR))

    sentences = json.loads(SENTENCES.read_text())
    rng.shuffle(sentences)
    n_held = max(60, len(sentences) // 4)
    for i, text in enumerate(sentences):
        held = i < n_held
        voices = sorted(plan.HELDOUT_MINI_VOICES) if held else [v for v in plan.MINI_VOICES if v not in plan.HELDOUT_MINI_VOICES]
        instrs = plan.HELDOUT_INSTR if held else plan.TRAIN_INSTR
        add("neg", "sentence", text, "gpt-4o-mini-tts", rng.choice(voices), "heldout" if held else "train", instr=rng.choice(instrs))
    return specs


def cmd_all() -> None:
    api = client()
    specs = build_specs()
    logger.info("clips planned: {}", len(specs))
    rows = []
    with ThreadPoolExecutor(8) as pool:
        for i, row in enumerate(pool.map(lambda s: tts(api, s), specs)):
            if row:
                rows.append(row)
            if i % 100 == 0:
                logger.info("{}/{} spent~{:.2f} USD", i, len(specs), spent["usd"])
    MANIFEST.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    logger.info("done: {} clips, estimated spend {:.2f} USD", len(rows), spent["usd"])


def main() -> int:
    commands = {"sentences": cmd_sentences, "all": cmd_all}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        logger.error("usage: python -m s1.ko.synth [{}]", "|".join(commands))
        return 2
    commands[sys.argv[1]]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
