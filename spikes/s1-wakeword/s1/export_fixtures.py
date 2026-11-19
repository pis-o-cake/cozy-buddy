"""Export tiny PCM fixtures and the Python reference scores for the Kotlin parity test.

Usage (from spikes/s1-wakeword): uv run python -m s1.export_fixtures <output_dir> [--ko]
With --ko only the experimental Korean keyword fixture is written (positive_ko.pcm, reference_scores_ko.json). The
Korean negative is the existing negative.pcm in <output_dir>. A missing TTS clip is synthesized (needs OPENAI_API_KEY via the repo .env).
The hub JVM test replays the PCM through the Kotlin pipeline and compares per-chunk scores with reference_scores.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from s1.evaluate import ENV_PATH, MODEL_DIR, NEGATIVE_SENTENCES, NEGATIVE_VOICES, POSITIVE_PHRASES, clip_path, synthesize
from s1.pipeline import WakePipeline

SAMPLE_RATE = 16_000
LEAD_S = 0.4
TAIL_S = 0.8
POSITIVE = ("hey_jarvis_en", "alloy", 1.0)
NEGATIVE_INDEX = 16
POSITIVE_KO = ("jarvis_ko", "onyx", 1.0)
KO_KEYWORD_FILE = "jarvis_ko_c.onnx"


def padded(pcm: np.ndarray) -> np.ndarray:
    lead = np.zeros(int(LEAD_S * SAMPLE_RATE), dtype=np.int16)
    tail = np.zeros(int(TAIL_S * SAMPLE_RATE), dtype=np.int16)
    return np.concatenate([lead, pcm, tail])


def ko_positive() -> np.ndarray:
    """Cached TTS clip of the Korean keyword; synthesized once through the spike's OpenAI client when absent."""
    key, voice, speed = POSITIVE_KO
    text = POSITIVE_PHRASES[key]
    if not clip_path(text, voice, speed).exists():
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv(ENV_PATH, override=False)
        return synthesize(OpenAI(), text, voice, speed)
    return np.load(clip_path(text, voice, speed))


def export_ko(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    pipeline = WakePipeline(MODEL_DIR, KO_KEYWORD_FILE)
    positive = padded(ko_positive())
    negative = np.fromfile(out / "negative.pcm", dtype="<i2")
    positive.astype("<i2").tofile(out / "positive_ko.pcm")
    reference = {
        "positive": {"samples": int(positive.shape[0]), "scores": [round(s, 6) for s in pipeline.scores(positive)]},
        "negative": {"samples": int(negative.shape[0]), "scores": [round(s, 6) for s in pipeline.scores(negative)]},
    }
    (out / "reference_scores_ko.json").write_text(json.dumps(reference))
    print({k: (v["samples"], max(v["scores"])) for k, v in reference.items()})
    return 0


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    key, voice, speed = POSITIVE
    positive = padded(np.load(clip_path(POSITIVE_PHRASES[key], voice, speed)))
    negative_voice = NEGATIVE_VOICES[NEGATIVE_INDEX % len(NEGATIVE_VOICES)]
    negative = padded(np.load(clip_path(NEGATIVE_SENTENCES[NEGATIVE_INDEX], negative_voice, 1.0)))
    pipeline = WakePipeline(MODEL_DIR)
    reference = {}
    for name, pcm in (("positive", positive), ("negative", negative)):
        pcm.astype("<i2").tofile(out / f"{name}.pcm")
        reference[name] = {"samples": int(pcm.shape[0]), "scores": [round(s, 6) for s in pipeline.scores(pcm)]}
    (out / "reference_scores.json").write_text(json.dumps(reference))
    print({k: (v["samples"], max(v["scores"])) for k, v in reference.items()})
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1])
    sys.exit(export_ko(target) if "--ko" in sys.argv[2:] else main(target))
