"""Build embedding datasets (train / val / test) from TTS clips, FLEURS ko speech and ESC-50 noise.

Usage (on the training host, from the code root):
    python -m s1.ko.build_features --root ~/dev/cozy-wakeword --models ~/dev/cozy-wakeword/models

Layout under --root: data/ko/{manifest.jsonl,pos,neg}, ext/{fleurs_*,esc}, feat/{train,val,test}.npz.
Every clip is passed through the frozen openWakeWord mel + embedding models, one embedding row per 80 ms.
Row labels: 1 wake word just ended, 0 background, -1 ambiguous (inside or right after the word, ignored in training).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing as mp
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from loguru import logger
from scipy.signal import resample_poly

from s1.ko import augment as aug
from s1.ko import plan
from s1.ko.frontend import CHUNK, FrontEnd

SR = 16_000
LEAD_RANGE_TRAIN = (0.5, 1.5)
LEAD_EVAL = 1.0
TAIL_S = 1.5
POS_BEFORE, POS_AFTER, AMBIG_AFTER = 1, 3, 16

_state: dict = {}


@dataclass
class Item:
    group: str
    source: tuple  # ("wav", path) | ("fleurs", path) | ("esc", index) | ("silence", seconds)
    seed: int
    positive: bool = False
    word_end_s: float | None = None
    cond: str = "train"  # train | clean | noisy | tv
    kind: str | None = None
    bank: str = "train"


def load_pcm(source: tuple) -> np.ndarray:
    tag, ref = source
    if tag == "wav":
        return aug.read_wav(Path(ref))
    if tag == "fleurs":
        data, rate = sf.read(ref, dtype="float32")
        return np.clip(data * 32767, -32768, 32767).astype(np.int16)
    if tag == "esc":
        return _state["esc"][ref]
    if tag == "silence":
        return np.zeros(int(ref * SR), dtype=np.int16)
    raise ValueError(tag)


def render(item: Item) -> tuple[np.ndarray, int, int]:
    rng = np.random.default_rng(item.seed)
    bank = _state["bank"][item.bank]
    base = load_pcm(item.source)
    start, end = aug.speech_bounds(base) if item.positive else (0, base.shape[0])
    if item.word_end_s is not None:
        end = start + int(item.word_end_s * SR)
    if item.cond == "train":
        pcm = aug.augment(base, rng, bank, kind=item.kind)
    elif item.cond == "clean":
        pcm = aug.to_pcm(base.astype(np.float64), -6.0)
    elif item.cond == "noisy":
        pcm = aug.augment(base, rng, bank, p_reverb=1.0, p_noise=1.0, snr_range=(5.0, 15.0), p_lowpass=0.0)
    elif item.cond == "tv":
        pcm = aug.augment(base, rng, bank, p_reverb=0.3, p_noise=1.0, snr_range=(0.0, 10.0), p_lowpass=0.0, kind=item.kind or "music")
    else:
        raise ValueError(item.cond)
    lead = int((rng.uniform(*LEAD_RANGE_TRAIN) if item.cond == "train" else LEAD_EVAL) * SR)
    padded = np.concatenate([np.zeros(lead, np.int16), pcm, np.zeros(int(TAIL_S * SR), np.int16)])
    return padded, lead + start, lead + end


def work(item: Item) -> tuple[Item, np.ndarray, np.ndarray, float]:
    padded, start, end = render(item)
    emb = _state["fe"].embeddings(padded)
    labels = np.zeros(emb.shape[0], dtype=np.int8)
    if item.positive:
        r_start = start // CHUNK
        r_end = math.ceil(end / CHUNK) - 1
        labels[r_start + 2 : r_end - POS_BEFORE + 1] = -1
        labels[r_end + POS_AFTER + 1 : r_end + AMBIG_AFTER + 1] = -1
        labels[max(0, r_end - POS_BEFORE) : r_end + POS_AFTER + 1] = 1
    seconds = (padded.shape[0] - int((1.0 + TAIL_S) * SR if item.cond != "train" else 0)) / SR
    return item, emb.astype(np.float16), labels, seconds


def init_worker(model_dir: str) -> None:
    _state["fe"] = FrontEnd(Path(model_dir))


def read_manifest(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "data/ko/manifest.jsonl").read_text().splitlines()]


def load_esc(root: Path) -> tuple[dict[int, np.ndarray], dict[str, list[int]]]:
    base = root / "ext/esc/ESC-50-master"
    clips, folds = {}, {"train": [], "heldout": []}
    with open(base / "meta/esc50.csv") as f:
        for i, row in enumerate(csv.DictReader(f)):
            data, rate = sf.read(base / "audio" / row["filename"], dtype="float32")
            clips[i] = np.clip(resample_poly(data, 160, 441) * 32767, -32768, 32767).astype(np.int16)
            folds["heldout" if row["fold"] == "5" else "train"].append(i)
    return clips, folds


def fleurs_files(root: Path, name: str) -> list[str]:
    return sorted(str(p) for p in (root / f"ext/fleurs_{name}").rglob("*.wav"))


def build_items(root: Path, esc_folds: dict[str, list[int]]) -> dict[str, list[Item]]:
    rows = read_manifest(root)
    seed = iter(range(10_000, 10_000_000))
    out: dict[str, list[Item]] = {"train": [], "val": [], "test": []}

    # matched standalone "자비스" durations give the word end of command clips ("자비스 불 꺼줘")
    standalone = {}
    for r in rows:
        if r["label"] == "wake" and r["text"] == "자비스" and r["model"] == "gpt-4o-mini-tts":
            s, e = aug.speech_bounds(aug.read_wav(root / "data/ko" / r["file"]))
            standalone[(r["voice"], r["instr"])] = (e - s) / SR

    for r in rows:
        path = root / "data/ko" / r["file"]
        split = "train" if r["split"] == "train" else "test"
        if r["kind"] == "pos":
            word_end = None
            if r["label"] == "wake_cmd":
                word_end = standalone.get((r["voice"], r["instr"]))
                if word_end is None:
                    continue
            if split == "train":
                group = "pos_cmd" if word_end else "pos"
                out["train"] += [Item(group, ("wav", path), next(seed), True, word_end) for _ in range(6)]
            else:
                group = "pos_cmd" if word_end else "pos"
                for cond in ("clean", "noisy"):
                    out["test"].append(Item(f"{group}_{cond}", ("wav", path), next(seed), True, word_end, cond))
        else:
            group = "neg_conf" if r["label"].startswith("confusable") else "neg_tts"
            test_group = group if group == "neg_tts" else ("neg_conf_near" if r["text"] in plan.NEAR_HOMOPHONES else "neg_conf_real")
            if split == "train":
                out["train"] += [Item(group, ("wav", path), next(seed)) for _ in range(8 if group == "neg_conf" else 3)]
            else:
                for cond in ("clean", "noisy"):
                    out["test"].append(Item(f"{test_group}_{cond}", ("wav", path), next(seed), cond=cond))

    for f in fleurs_files(root, "train"):
        out["train"] += [Item("neg_fleurs", ("fleurs", f), next(seed)), Item("neg_fleurs_tv", ("fleurs", f), next(seed), cond="train", kind="music")]
    for i in esc_folds["train"]:
        out["train"].append(Item("neg_esc", ("esc", i), next(seed)))
    out["train"] += [Item("neg_silence", ("silence", 5.0), next(seed)) for _ in range(60)]

    for f in fleurs_files(root, "dev"):
        out["val"] += [Item("neg_fleurs_clean", ("fleurs", f), next(seed), cond="clean"), Item("neg_fleurs_tv", ("fleurs", f), next(seed), cond="tv")]
    for f in fleurs_files(root, "test"):
        out["test"] += [Item("neg_fleurs_clean", ("fleurs", f), next(seed), cond="clean"), Item("neg_fleurs_tv", ("fleurs", f), next(seed), cond="tv")]
    for i in esc_folds["heldout"]:
        out["test"].append(Item("neg_esc", ("esc", i), next(seed), cond="clean"))
    return out


def save(path: Path, results: list) -> None:
    items, embs, labels, seconds = zip(*results)
    offsets = np.cumsum([0] + [e.shape[0] for e in embs])
    np.savez(
        path,
        emb=np.concatenate(embs),
        labels=np.concatenate(labels),
        offsets=offsets,
        group=np.array([i.group for i in items]),
        seconds=np.array(seconds, dtype=np.float32),
    )
    logger.info("saved {}: {} clips, {} rows", path.name, len(items), offsets[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--only", nargs="*", default=None, help="build only these splits")
    args = parser.parse_args()
    root = args.root.expanduser()
    (root / "feat").mkdir(exist_ok=True)

    esc, folds = load_esc(root)
    speech = {name: [aug.to_pcm(sf.read(f, dtype="float32")[0].astype(np.float64), -6.0) for f in fleurs_files(root, name)[:150]] for name in ("train", "dev", "test")}
    _state["esc"] = esc
    _state["bank"] = {
        "train": aug.NoiseBank([esc[i] for i in folds["train"]], speech["train"]),
        "heldout": aug.NoiseBank([esc[i] for i in folds["heldout"]], speech["test"]),
    }
    items = build_items(root, folds)
    for item in items["test"]:
        item.bank = "heldout"
    with mp.get_context("fork").Pool(args.workers, init_worker, (str(args.models),)) as pool:
        for name, lst in items.items():
            if args.only and name not in args.only:
                continue
            logger.info("{}: {} items", name, len(lst))
            save(root / f"feat/{name}.npz", list(pool.imap(work, lst, chunksize=8)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
