"""Batched openWakeWord front end (melspectrogram -> embedding) for training and evaluation.

Produces one 96-dim embedding per 80 ms step, the same stream the hub feeds its classifier.
Batch and streaming extraction are checked against s1.pipeline.WakePipeline by `parity`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

CHUNK = 1280
MEL_BINS = 32
WINDOW = 76
MEL_CONTEXT = 480
EMBED_DIM = 96
CLF_FRAMES = 16
WARMUP = 5


class FrontEnd:
    def __init__(self, model_dir: Path, threads: int = 1) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        self._mel = ort.InferenceSession(str(model_dir / "melspectrogram.onnx"), options, providers=providers)
        self._embed = ort.InferenceSession(str(model_dir / "embedding_model.onnx"), options, providers=providers)

    def embeddings(self, pcm: np.ndarray) -> np.ndarray:
        """Embedding stream [T, 96] for an int16 clip; row i is the embedding after chunk i (80 ms each)."""
        remainder = (-pcm.shape[0]) % CHUNK
        audio = np.concatenate([pcm.astype(np.float32), np.zeros(remainder, dtype=np.float32)])
        # Mel per chunk with the 480-sample context, exactly like the streaming pipeline (frame alignment matters).
        frames = [np.ones((WINDOW, MEL_BINS), dtype=np.float32)]
        starts = []
        tail = np.zeros(0, dtype=np.float32)
        total = WINDOW
        for i in range(audio.shape[0] // CHUNK):
            samples = np.concatenate([tail, audio[i * CHUNK : (i + 1) * CHUNK]])
            tail = samples[-MEL_CONTEXT:]
            mel = self._mel.run(None, {"input": samples[None, :]})[0].squeeze() / 10.0 + 2.0
            frames.append(mel.reshape(-1, MEL_BINS).astype(np.float32))
            total += frames[-1].shape[0]
            starts.append(total)
        mel_all = np.vstack(frames)
        starts = [t - WINDOW for t in starts]
        windows = np.stack([mel_all[s : s + WINDOW] for s in starts])[..., None]
        out = [self._embed.run(None, {"input_1": windows[i : i + 256]})[0].reshape(-1, EMBED_DIM) for i in range(0, len(starts), 256)]
        return np.concatenate(out).astype(np.float32)


def windows(emb: np.ndarray) -> np.ndarray:
    """All classifier windows [N, 16, 96]; window i ends at embedding row i + 15, earlier rows are zero padded."""
    padded = np.vstack([np.zeros((CLF_FRAMES - 1, EMBED_DIM), dtype=np.float32), emb])
    return np.stack([padded[i : i + CLF_FRAMES] for i in range(emb.shape[0])]) if emb.shape[0] else np.zeros((0, CLF_FRAMES, EMBED_DIM), np.float32)
