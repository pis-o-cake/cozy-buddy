"""Evaluate classifier heads on held-out embeddings (val/test npz from build_features).

Usage: python -m s1.ko.evaluate_ko --root ~/dev/cozy-wakeword --models models/jarvis_ko.onnx models/hey_jarvis_v0.1.onnx --out results/ko_eval.json

Detection rule matches the hub: score >= threshold for 2 consecutive 80 ms chunks, 2 s refractory,
the first 5 predictions after stream start are zeroed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from loguru import logger

from s1.ko.frontend import CLF_FRAMES, EMBED_DIM

CONSECUTIVE = 2
REFRACTORY_ROWS = 25
WARMUP_ROWS = 5
THRESHOLDS = [round(0.1 + 0.05 * i, 2) for i in range(0, 18)]
FA_GROUPS = ("neg_fleurs_clean", "neg_fleurs_tv", "neg_tts_clean", "neg_tts_noisy", "neg_esc")
CONFUSABLE_GROUPS = ("neg_conf_near_clean", "neg_conf_near_noisy", "neg_conf_real_clean", "neg_conf_real_noisy")
REAL_CONFUSABLE_GROUPS = ("neg_conf_real_clean", "neg_conf_real_noisy")
POSITIVE_GROUPS = ("pos_clean", "pos_noisy", "pos_cmd_clean", "pos_cmd_noisy")


def clip_scores(session: ort.InferenceSession, emb: np.ndarray) -> np.ndarray:
    padded = np.vstack([np.zeros((CLF_FRAMES - 1, EMBED_DIM), np.float32), emb.astype(np.float32)])
    name = session.get_inputs()[0].name
    out = np.array([session.run(None, {name: padded[i : i + CLF_FRAMES][None]})[0].reshape(-1)[0] for i in range(emb.shape[0])])
    out[:WARMUP_ROWS] = 0.0
    return out


def hits(scores: np.ndarray) -> np.ndarray:
    return np.minimum(scores[:-1], scores[1:]) if len(scores) > 1 else np.zeros(0)


def events(scores: np.ndarray, threshold: float) -> int:
    count, last = 0, -10**9
    for i in np.flatnonzero(hits(scores) >= threshold):
        if i - last >= REFRACTORY_ROWS:
            count += 1
            last = i
    return count


def load(path: Path) -> dict:
    z = np.load(path)
    return {"emb": z["emb"], "offsets": z["offsets"], "group": z["group"], "seconds": z["seconds"]}


def score_set(session: ort.InferenceSession, data: dict) -> list[np.ndarray]:
    o = data["offsets"]
    return [clip_scores(session, data["emb"][o[c] : o[c + 1]]) for c in range(len(o) - 1)]


def summarize(scores: list[np.ndarray], data: dict) -> dict:
    groups = sorted(set(data["group"]))
    peaks = {g: np.array([hits(s).max() if len(s) > 1 else 0.0 for s, gr in zip(scores, data["group"]) if gr == g]) for g in groups}
    hours = {g: float(sum(sec for sec, gr in zip(data["seconds"], data["group"]) if gr == g)) / 3600 for g in groups}
    table = {}
    for t in THRESHOLDS:
        row = {}
        for g in groups:
            if g.startswith("pos"):
                row[g] = round(float((peaks[g] >= t).mean()), 4)
            else:
                row[g] = int(sum(events(s, t) for s, gr in zip(scores, data["group"]) if gr == g))
        table[str(t)] = row
    return {
        "clips": {g: int(len(peaks[g])) for g in groups},
        "hours": {g: round(hours[g], 3) for g in groups},
        "peak_percentiles": {g: {p: round(float(np.percentile(peaks[g], p)), 4) for p in (5, 25, 50, 75, 95, 100)} for g in groups},
        "by_threshold": table,
    }


def pooled(summary: dict, t: float) -> dict:
    row = summary["by_threshold"][str(t)]
    clips = summary["clips"]
    pos = [g for g in POSITIVE_GROUPS[:2] if g in row]
    detect = sum(row[g] * clips[g] for g in pos) / max(1, sum(clips[g] for g in pos))
    fa = sum(row[g] for g in FA_GROUPS if g in row)
    hours = sum(summary["hours"][g] for g in FA_GROUPS if g in row)
    conf = sum(row[g] for g in CONFUSABLE_GROUPS if g in row)
    conf_real = sum(row[g] for g in REAL_CONFUSABLE_GROUPS if g in row)
    return {
        "detect": round(detect, 4),
        "false_accepts": fa,
        "fa_per_hour": round(fa / hours, 3) if hours else None,
        "confusable_accepts": conf,
        "confusable_real_word_accepts": conf_real,
    }


def acceptance(summary: dict) -> dict:
    for t in THRESHOLDS:
        p = pooled(summary, t)
        if p["false_accepts"] == 0 and p["confusable_accepts"] <= 1:
            return {"threshold": t, **p}
    return {"threshold": None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--models", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    sets = {name: load(root / f"feat/{name}.npz") for name in ("val", "test")}
    report: dict = {}
    for model in args.models:
        session = ort.InferenceSession(str(root / model), options, providers=["CPUExecutionProvider"])
        entry = {}
        for name, data in sets.items():
            summary = summarize(score_set(session, data), data)
            summary["pooled"] = {str(t): pooled(summary, t) for t in THRESHOLDS}
            summary["acceptance"] = acceptance(summary)
            entry[name] = summary
            logger.info("{} {} acceptance {}", model.name, name, summary["acceptance"])
        val_t = entry["val"]["acceptance"].get("threshold")
        entry["threshold_from_val"] = {"threshold": val_t, "test": entry["test"]["pooled"].get(str(val_t)) if val_t else None}
        report[model.stem] = entry
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
