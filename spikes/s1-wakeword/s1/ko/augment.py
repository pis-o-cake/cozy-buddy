"""Audio augmentation: synthetic RIRs, background noises (white, pink, babble, ESC-50, synthetic music), gain, lowpass."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

SR = 16_000


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1:
            raise ValueError(f"unsupported wav format: {path}")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()


def speech_bounds(pcm: np.ndarray, floor_db: float = -35.0) -> tuple[int, int]:
    """First and last sample of speech using a 20 ms frame energy gate relative to the clip peak."""
    frame = 320
    n = pcm.shape[0] // frame
    if n == 0:
        return 0, pcm.shape[0]
    rms = np.sqrt(np.mean(pcm[: n * frame].astype(np.float64).reshape(n, frame) ** 2, axis=1)) + 1e-9
    active = np.where(20 * np.log10(rms / rms.max()) > floor_db)[0]
    return int(active[0] * frame), int((active[-1] + 1) * frame)


def synthetic_rir(rng: np.random.Generator) -> np.ndarray:
    rt60 = rng.uniform(0.15, 0.9)
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    # Small rooms are darker at high frequencies; a one-pole lowpass imitates absorption.
    tail = sosfilt(butter(1, rng.uniform(2500, 7000), fs=SR, output="sos"), tail)
    rir = tail * rng.uniform(0.05, 0.3)
    rir[0] = 1.0
    return rir.astype(np.float32)


def reverb(pcm: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    wet = fftconvolve(pcm.astype(np.float32), synthetic_rir(rng))[: pcm.shape[0]]
    return wet * (np.abs(pcm).max() / (np.abs(wet).max() + 1e-9))


def white(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(n))
    spectrum /= np.sqrt(np.maximum(np.arange(spectrum.shape[0]), 1))
    return np.fft.irfft(spectrum, n)


def music(n: int, rng: np.random.Generator) -> np.ndarray:
    """Chord progression of harmonic tones with a slow envelope; a crude stand-in for TV background music."""
    out = np.zeros(n)
    t = np.arange(n) / SR
    step = int(rng.uniform(0.4, 1.2) * SR)
    root = rng.uniform(110, 330)
    for start in range(0, n, step):
        end = min(n, start + step)
        seg = t[start:end] - t[start]
        env = np.minimum(1.0, seg * 20) * np.exp(-seg * rng.uniform(0.5, 3))
        for ratio in rng.choice([1, 1.25, 1.5, 2, 2.5, 3], size=3, replace=False):
            f = root * ratio * rng.choice([1, 1.0, 0.5])
            out[start:end] += env * sum(np.sin(2 * np.pi * f * k * seg) / k for k in (1, 2, 3))
        if rng.random() < 0.3:
            root = rng.uniform(110, 330)
    return out


class NoiseBank:
    """Draws background noise of a requested length from synthetic generators and real clip pools."""

    def __init__(self, esc_clips: list[np.ndarray], speech_clips: list[np.ndarray]) -> None:
        self.esc = esc_clips
        self.speech = speech_clips

    def sample(self, n: int, rng: np.random.Generator, kind: str | None = None) -> np.ndarray:
        kind = kind or rng.choice(["white", "pink", "babble", "esc", "music"], p=[0.15, 0.2, 0.25, 0.25, 0.15])
        if kind == "white":
            return white(n, rng)
        if kind == "pink":
            return pink(n, rng)
        if kind == "music":
            return music(n, rng)
        if kind == "esc" and self.esc:
            clip = self.esc[rng.integers(len(self.esc))].astype(np.float64)
            return np.resize(clip, n) if clip.shape[0] < n else clip[(s := rng.integers(0, clip.shape[0] - n + 1)) : s + n]
        mix = np.zeros(n)
        for _ in range(rng.integers(3, 7)):
            clip = self.speech[rng.integers(len(self.speech))].astype(np.float64)
            clip = np.resize(clip, n)
            mix += np.roll(clip, int(rng.integers(0, n)))
        return mix


def mix_at_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    speech_rms = np.sqrt(np.mean(speech.astype(np.float64) ** 2)) + 1e-9
    noise_rms = np.sqrt(np.mean(noise**2)) + 1e-9
    return speech + noise * (speech_rms / (noise_rms * 10 ** (snr_db / 20)))


def to_pcm(x: np.ndarray, peak_db: float) -> np.ndarray:
    x = x / (np.abs(x).max() + 1e-9) * 32767 * 10 ** (peak_db / 20)
    return np.clip(x, -32768, 32767).astype(np.int16)


def augment(
    pcm: np.ndarray,
    rng: np.random.Generator,
    bank: NoiseBank,
    *,
    p_reverb: float = 0.5,
    p_noise: float = 0.8,
    snr_range: tuple[float, float] = (0.0, 25.0),
    p_lowpass: float = 0.2,
    kind: str | None = None,
) -> np.ndarray:
    x = pcm.astype(np.float64)
    if rng.random() < p_reverb:
        x = reverb(x, rng)
    if rng.random() < p_lowpass:
        x = sosfilt(butter(2, rng.uniform(3500, 7000), fs=SR, output="sos"), x)
    if rng.random() < p_noise:
        x = mix_at_snr(x, bank.sample(x.shape[0], rng, kind), rng.uniform(*snr_range))
    return to_pcm(x, rng.uniform(-24.0, -3.0))
