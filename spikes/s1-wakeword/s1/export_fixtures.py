"""Export tiny PCM fixtures and the Python reference scores for the Kotlin parity test.

Usage (from spikes/s1-wakeword): uv run python -m s1.export_fixtures <output_dir>
The hub JVM test replays the PCM through the Kotlin pipeline and compares per-chunk scores with reference_scores.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from s1.evaluate import MODEL_DIR, NEGATIVE_SENTENCES, NEGATIVE_VOICES, POSITIVE_PHRASES, clip_path
from s1.pipeline import WakePipeline

SAMPLE_RATE = 16_000
LEAD_S = 0.4
TAIL_S = 0.8
POSITIVE = ("hey_jarvis_en", "alloy", 1.0)
NEGATIVE_INDEX = 16


def padded(pcm: np.ndarray) -> np.ndarray:
    lead = np.zeros(int(LEAD_S * SAMPLE_RATE), dtype=np.int16)
    tail = np.zeros(int(TAIL_S * SAMPLE_RATE), dtype=np.int16)
    return np.concatenate([lead, pcm, tail])


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
    sys.exit(main(Path(sys.argv[1])))
