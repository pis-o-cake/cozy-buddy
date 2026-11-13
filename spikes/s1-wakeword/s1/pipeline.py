"""openWakeWord streaming pipeline (mel -> embedding -> classifier), re-implemented for parity checks.

The hub's Kotlin pipeline must produce the same scores as this module.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
import onnxruntime as ort

CHUNK_SAMPLES = 1280
MEL_CONTEXT_SAMPLES = 160 * 3
MEL_BINS = 32
EMBED_WINDOW_FRAMES = 76
CLASSIFIER_FRAMES = 16
EMBED_DIM = 96
WARMUP_PREDICTIONS = 5


class WakePipeline:
    """Streaming scorer. One call to process_chunk per 80 ms of audio."""

    def __init__(self, model_dir: Path, keyword_file: str = "hey_jarvis_v0.1.onnx") -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        self._mel = ort.InferenceSession(str(model_dir / "melspectrogram.onnx"), options, providers=providers)
        self._embed = ort.InferenceSession(str(model_dir / "embedding_model.onnx"), options, providers=providers)
        self._clf = ort.InferenceSession(str(model_dir / keyword_file), options, providers=providers)
        self.reset()

    def reset(self) -> None:
        self._tail = np.zeros(0, dtype=np.float32)
        self._mel_frames = np.ones((EMBED_WINDOW_FRAMES, MEL_BINS), dtype=np.float32)
        self._features: deque[np.ndarray] = deque(
            [np.zeros(EMBED_DIM, dtype=np.float32)] * CLASSIFIER_FRAMES, maxlen=CLASSIFIER_FRAMES
        )
        self._predictions = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        """Feed exactly 1280 int16 samples and return the keyword score."""
        if chunk.shape[0] != CHUNK_SAMPLES:
            raise ValueError("chunk must be 1280 samples")
        samples = np.concatenate([self._tail, chunk.astype(np.float32)])
        self._tail = samples[-MEL_CONTEXT_SAMPLES:]
        mel = self._mel.run(None, {"input": samples[None, :]})[0].squeeze() / 10.0 + 2.0
        self._mel_frames = np.vstack([self._mel_frames, mel])[-EMBED_WINDOW_FRAMES * 4 :]
        window = self._mel_frames[-EMBED_WINDOW_FRAMES:].astype(np.float32)[None, :, :, None]
        embedding = self._embed.run(None, {"input_1": window})[0].reshape(-1)
        self._features.append(embedding)
        features = np.stack(self._features)[None, :, :].astype(np.float32)
        score = float(self._clf.run(None, {self._clf.get_inputs()[0].name: features})[0].reshape(-1)[0])
        self._predictions += 1
        return 0.0 if self._predictions <= WARMUP_PREDICTIONS else score

    def scores(self, pcm: np.ndarray) -> list[float]:
        """Scores for every 1280-sample chunk of an int16 clip (the tail is zero padded)."""
        self.reset()
        remainder = (-pcm.shape[0]) % CHUNK_SAMPLES
        padded = np.concatenate([pcm, np.zeros(remainder, dtype=pcm.dtype)])
        return [self.process_chunk(padded[i : i + CHUNK_SAMPLES]) for i in range(0, padded.shape[0], CHUNK_SAMPLES)]
