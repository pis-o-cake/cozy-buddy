"""Offline S1 evaluation: synthesize clips with OpenAI TTS, score them with the openWakeWord ONNX pipeline.

Usage (from spikes/s1-wakeword):
    uv run python -m s1.evaluate synth   # TTS clips -> .cache (needs OPENAI_API_KEY via the repo .env)
    uv run python -m s1.evaluate score    # score cached clips -> results/results.json

Model files are read from hub/app/src/main/assets/wakeword so the evaluation uses exactly what ships.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from loguru import logger
from scipy.signal import lfilter, resample_poly

from s1.pipeline import WakePipeline

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
ENV_PATH = Path("/Users/cheol/dev/private/cozy-buddy/.env")
MODEL_DIR = REPO / "hub/app/src/main/assets/wakeword"
CACHE = ROOT / ".cache"
RESULTS = ROOT / "results/results.json"

TTS_MODEL = "tts-1"
TTS_USD_PER_MILLION_CHARS = 15.0
BUDGET_USD = 0.5
SAMPLE_RATE = 16_000
LEAD_SILENCE_S = 1.0
TAIL_SILENCE_S = 1.5
THRESHOLDS = [round(0.2 + 0.05 * i, 2) for i in range(0, 17)]
POSITIVE_VOICES = ["alloy", "echo", "fable", "nova", "onyx", "shimmer"]
NEGATIVE_VOICES = ["alloy", "nova", "onyx"]
SPEEDS = [1.0, 1.15]
SNR_DB_NOISY = 10.0
CONSECUTIVE_HITS = 2

POSITIVE_PHRASES = {
    "hey_jarvis_en": "Hey Jarvis",
    "hey_jarvis_ko": "헤이 자비스",
    "jarvis_ko": "자비스",
    "hey_jarvis_ko_cmd": "헤이 자비스, 알람 꺼줘",
}

NEGATIVE_SENTENCES = [
    "오늘 저녁에는 뭐 먹을까 고민이에요",
    "내일 아침 일곱 시에 일어나야 해",
    "거실 불 좀 꺼 줄래",
    "날씨가 많이 추워졌네요 외투 챙기세요",
    "아이들 학교 보내고 장 보러 다녀올게",
    "이번 주말에 할머니 댁에 가기로 했어",
    "빨래 다 돌았으니까 건조기로 옮겨 주세요",
    "세탁기 돌리는 소리가 너무 시끄럽다",
    "엄마 이거 어떻게 하는 거예요",
    "지금 몇 시야 벌써 열 시가 넘었네",
    "냉장고에 우유가 다 떨어졌어",
    "리모컨 어디 있지 소파 밑에 있나",
    "오늘 뉴스에서 내일 비가 온다고 하더라",
    "저녁 먹고 산책이나 갈까요",
    "이 노래 참 좋다 볼륨 좀 올려 봐",
    "배달 음식 시켰는데 아직도 안 오네",
    "회사에서 회의가 길어져서 조금 늦을 것 같아",
    "아빠 오늘은 일찍 들어오세요",
    "숙제 다 했으면 텔레비전 봐도 돼",
    "내일 병원 예약 열 시 반이니까 잊지 마",
    "고양이 밥 줬어 아직 안 줬어",
    "여보 이번 휴가 때 어디로 갈지 정했어",
    "창문 좀 닫아 줄래 바람이 너무 세다",
    "아까 택배 아저씨가 다녀가셨어요",
    "커피 한 잔 마실래요 아니면 차 드릴까요",
    "서비스 센터에 전화해서 물어봐야겠다",
    "자바스크립트 공부하는 중인데 어렵네",
    "하이 재즈 음악 좀 틀어 줘",
    "헤이 구글 오늘 날씨 알려 줘",
    "자 비서 선생님 이쪽으로 오세요",
    "재비 한 마리가 처마 밑에 집을 지었어",
    "야 자비로운 마음으로 이해해 주자",
    "그 영화 주인공 이름이 뭐였더라 아이언맨 맞나",
    "Hey Travis, what time is it",
    "Hey Siri set a timer for ten minutes",
    "The service was great and the dishes were delicious",
    "알람 소리가 너무 작아서 못 들었어요",
    "우리 집 강아지가 방금 소파에서 떨어졌어",
    "내일 발표 자료 준비는 다 끝났니",
    "오늘 하루도 정말 수고 많았어요 푹 쉬세요",
]


@dataclass(frozen=True)
class Clip:
    kind: str
    label: str
    voice: str
    speed: float
    pcm: np.ndarray


def clip_path(text: str, voice: str, speed: float) -> Path:
    digest = hashlib.sha256(f"{TTS_MODEL}|{voice}|{speed}|{text}".encode()).hexdigest()[:16]
    return CACHE / f"{digest}.npy"


def synthesize(client, text: str, voice: str, speed: float) -> np.ndarray:
    path = clip_path(text, voice, speed)
    if path.exists():
        return np.load(path)
    response = client.audio.speech.create(
        model=TTS_MODEL, voice=voice, input=text, response_format="pcm", speed=speed
    )
    pcm24 = np.frombuffer(response.content, dtype="<i2").astype(np.float32)
    pcm16 = np.clip(resample_poly(pcm24, 2, 3), -32768, 32767).astype(np.int16)
    CACHE.mkdir(exist_ok=True)
    np.save(path, pcm16)
    return pcm16


def plan() -> list[tuple[str, str, str, float]]:
    items = []
    for key, text in POSITIVE_PHRASES.items():
        for voice in POSITIVE_VOICES:
            for speed in SPEEDS:
                items.append(("positive", key, text, voice, speed))
    for index, text in enumerate(NEGATIVE_SENTENCES):
        voice = NEGATIVE_VOICES[index % len(NEGATIVE_VOICES)]
        items.append(("negative", f"neg{index:02d}", text, voice, 1.0))
    return items


def cmd_synth() -> int:
    load_dotenv(ENV_PATH, override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set")
        return 1
    from openai import OpenAI

    client = OpenAI()
    chars = 0
    for kind, label, text, voice, speed in plan():
        if clip_path(text, voice, speed).exists():
            continue
        chars += len(text)
        cost = chars * TTS_USD_PER_MILLION_CHARS / 1e6
        if cost > BUDGET_USD:
            logger.error("Budget exceeded: {:.3f} USD", cost)
            return 1
        synthesize(client, text, voice, speed)
        logger.info("synthesized {} {} {} x{}", kind, label, voice, speed)
    logger.info("TTS characters billed this run: {} (~{:.4f} USD)", chars, chars * TTS_USD_PER_MILLION_CHARS / 1e6)
    return 0


def load_clips() -> list[Clip]:
    clips = []
    for kind, label, text, voice, speed in plan():
        path = clip_path(text, voice, speed)
        if not path.exists():
            raise FileNotFoundError(f"missing clip for {label}; run `synth` first")
        clips.append(Clip(kind, label, voice, speed, np.load(path)))
    return clips


def pad(pcm: np.ndarray) -> np.ndarray:
    lead = np.zeros(int(LEAD_SILENCE_S * SAMPLE_RATE), dtype=np.int16)
    tail = np.zeros(int(TAIL_SILENCE_S * SAMPLE_RATE), dtype=np.int16)
    return np.concatenate([lead, pcm, tail])


def add_room_noise(pcm: np.ndarray, snr_db: float, seed: int) -> np.ndarray:
    """Pink-ish noise (single pole low-pass of white noise) at the given SNR over the speech RMS."""
    rng = np.random.default_rng(seed)
    noise = lfilter([1.0], [1.0, -0.95], rng.standard_normal(pcm.shape[0]))
    speech_rms = np.sqrt(np.mean(pcm.astype(np.float64) ** 2)) + 1e-9
    noise *= speech_rms / (np.sqrt(np.mean(noise**2)) * 10 ** (snr_db / 20))
    return np.clip(pcm + noise, -32768, 32767).astype(np.int16)


def peak_score(pipeline: WakePipeline, pcm: np.ndarray) -> float:
    """Highest score sustained for CONSECUTIVE_HITS predictions in a row, matching the engine's detection rule."""
    scores = pipeline.scores(pcm)
    windows = [min(scores[i : i + CONSECUTIVE_HITS]) for i in range(len(scores) - CONSECUTIVE_HITS + 1)]
    return max(windows)


def rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def cmd_score() -> int:
    clips = load_clips()
    pipeline = WakePipeline(MODEL_DIR)
    positives = [c for c in clips if c.kind == "positive"]
    negatives = [c for c in clips if c.kind == "negative"]

    rows = []
    for index, clip in enumerate(positives):
        for condition in ("clean", "noisy"):
            audio = pad(clip.pcm)
            if condition == "noisy":
                audio = add_room_noise(audio, SNR_DB_NOISY, seed=index)
            rows.append((clip.label, clip.voice, clip.speed, condition, peak_score(pipeline, audio)))
        logger.info("scored {} {} x{}", clip.label, clip.voice, clip.speed)

    neg_peaks = [(c.label, c.voice, peak_score(pipeline, pad(c.pcm))) for c in negatives]
    neg_peaks_noisy = [
        (c.label, c.voice, peak_score(pipeline, add_room_noise(pad(c.pcm), SNR_DB_NOISY, seed=1000 + i)))
        for i, c in enumerate(negatives)
    ]
    negative_seconds = sum(c.pcm.shape[0] for c in negatives) / SAMPLE_RATE

    phrases = sorted({r[0] for r in rows})
    voices = sorted({r[1] for r in rows})
    by_threshold = {}
    for threshold in THRESHOLDS:
        entry: dict = {"phrase": {}, "voice": {}}
        for condition in ("clean", "noisy"):
            selected = [r for r in rows if r[3] == condition]
            entry[f"overall_{condition}"] = rate(sum(r[4] >= threshold for r in selected), len(selected))
            entry["phrase"][condition] = {
                p: rate(sum(r[4] >= threshold for r in selected if r[0] == p), sum(1 for r in selected if r[0] == p))
                for p in phrases
            }
            entry["voice"][condition] = {
                v: rate(sum(r[4] >= threshold for r in selected if r[1] == v), sum(1 for r in selected if r[1] == v))
                for v in voices
            }
        entry["false_accepts_clean"] = sum(n[2] >= threshold for n in neg_peaks)
        entry["false_accepts_noisy"] = sum(n[2] >= threshold for n in neg_peaks_noisy)
        by_threshold[str(threshold)] = entry

    result = {
        "model": "hey_jarvis_v0.1",
        "tts_model": TTS_MODEL,
        "positives": len(positives),
        "negatives": len(negatives),
        "negative_audio_seconds": round(negative_seconds, 1),
        "noisy_snr_db": SNR_DB_NOISY,
        "consecutive_hits": CONSECUTIVE_HITS,
        "note": "TTS speech, not real far-field recordings; use for relative threshold choice only",
        "by_threshold": by_threshold,
        "positive_peaks": [
            {"phrase": r[0], "voice": r[1], "speed": r[2], "condition": r[3], "peak": round(r[4], 4)} for r in rows
        ],
        "negative_peaks": [
            {"label": n[0], "voice": n[1], "peak": round(n[2], 4)} for n in neg_peaks
        ],
        "negative_peaks_noisy": [
            {"label": n[0], "voice": n[1], "peak": round(n[2], 4)} for n in neg_peaks_noisy
        ],
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    logger.info("wrote {}", RESULTS)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["synth", "score"])
    args = parser.parse_args()
    return cmd_synth() if args.command == "synth" else cmd_score()


if __name__ == "__main__":
    sys.exit(main())
