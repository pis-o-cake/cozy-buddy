"""Runner for the streaming-STT spike (S2c): sequential, raw.jsonl + summary.json."""
from __future__ import annotations

import asyncio
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from s2 import pricing
from s2.audio import load_commands, read_pcm, synthesize_all
from s2.config import Models
from s2.normalize import normalize
from s2.path_stream import KEYWORDS, run_batch, run_stream
from s2.runner import PAUSE_S, STT_PROMPT, _raw_norm, _rate, pct

LIVE = "gpt-live-transcribe"
GT = "gpt-transcribe"
SAMPLE_TIMEOUT_S = 60

# path name -> (kind, model, prompt, keywords, delay)
PATHS: dict[str, tuple[str, str, str | None, list[str] | None, str | None]] = {
    "batch_prompt": ("batch", "gpt-4o-transcribe", STT_PROMPT, None, None),
    "batch_gt_prompt": ("batch", GT, STT_PROMPT, None, None),
    "stream": ("stream", LIVE, None, None, None),
    "stream_prompt": ("stream", LIVE, STT_PROMPT, None, None),
    "stream_kw": ("stream", LIVE, None, KEYWORDS, None),
    "stream_minimal": ("stream", LIVE, STT_PROMPT, None, "minimal"),
    "stream_gt_prompt": ("stream", GT, STT_PROMPT, None, None),
}


async def _sample(client: AsyncOpenAI, path: str, pcm: bytes) -> dict[str, Any]:
    kind, model, prompt, kws, delay = PATHS[path]
    last: Exception | None = None
    for attempt in range(2):
        try:
            coro = run_stream(pcm, model, prompt, kws, delay) if kind == "stream" else run_batch(
                client, pcm, model, prompt)
            rec = await asyncio.wait_for(coro, SAMPLE_TIMEOUT_S)
            rec["attempts"] = attempt + 1
            return rec
        except Exception as exc:  # noqa: BLE001 - transient errors are recorded, not fatal
            last = exc
            logger.warning("Path {} attempt {} failed: {!r}", path, attempt + 1, exc)
            await asyncio.sleep(1.0)
    return {"error": repr(last), "attempts": 2, "cost_usd": 0.0}


async def run(paths: list[str], repeat: int, budget: float, limit: int | None, out_root: Path) -> Path:
    client = AsyncOpenAI()
    commands = load_commands(limit)
    await synthesize_all(client, Models().tts, commands)
    out_dir = out_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True)
    spent = 0.0
    records: list[dict[str, Any]] = []
    with (out_dir / "raw.jsonl").open("w", encoding="utf-8") as raw:
        for rep in range(repeat):
            for cmd in commands:
                pcm = read_pcm(cmd["id"])
                for path in paths:
                    rec = await _sample(client, path, pcm)
                    rec.update(path=path, command_id=cmd["id"], category=cmd["category"],
                               command_text=cmd["text"], repeat=rep)
                    spent += rec.get("cost_usd", 0.0)
                    records.append(rec)
                    raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    raw.flush()
                    logger.info("{} {} rep={} final={} spent=${:.3f}", path, cmd["id"], rep,
                                _fmt(rec.get("t_end_to_final")), spent)
                    if spent > budget:
                        logger.error("Aborting: spend ${:.2f} exceeds budget ${:.2f}", spent, budget)
                        return _finish(records, spent, out_dir)
                    await asyncio.sleep(PAUSE_S)
    return _finish(records, spent, out_dir)


def _finish(records: list[dict[str, Any]], spent: float, out_dir: Path) -> Path:
    summary = summarize(records, spent)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for path, e in summary["paths"].items():
        print(f"{path:18} n={e['samples']} fail={e['failures']} final p50={_fmt(e['t_end_to_final_p50_s'])} "
              f"p95={_fmt(e['t_end_to_final_p95_s'])} first_delta p50={_fmt(e['t_end_to_first_delta_p50_s'])} "
              f"exact={e['transcript_exact_rate']} usd/cmd={e['cost_per_command_usd']}")
    return out_dir


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}s"


def summarize(records: list[dict[str, Any]], spent: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "spike": "S2c streaming STT",
        "path_definitions": {k: {"kind": v[0], "model": v[1], "prompt": v[2], "keywords": v[3], "delay": v[4]}
                             for k, v in PATHS.items() if any(r["path"] == k for r in records)},
        "api": {"stream": "Realtime WebSocket wss://api.openai.com/v1/realtime?intent=transcription, "
                          "session.type=transcription, turn_detection=null, manual input_audio_buffer.commit",
                "batch": "POST /v1/audio/transcriptions (file upload after speech end)"},
        "timing": {"t_end": "end of speech: last 20 ms chunk fully paced out (stream) / instant before upload "
                            "starts (batch). Connection setup excluded (session assumed open since wake word).",
                   "chunk_ms": 20},
        "normaliser": "s2/normalize.py (punctuation, spaces, sino-korean/digit numbers)",
        "pricing": {"url": pricing.PRICING_URL, "date": "2026-11-19",
                    "per_min": {m: pricing.STT_PER_MIN[m] for m in (LIVE, GT, "gpt-4o-transcribe")},
                    "note": "gpt-4o-transcribe per-minute is the page's estimate from token prices"},
        "limitations": ["Input audio is synthetic TTS speech (24 kHz, gpt-4o-mini-tts echo).",
                        "Latency measured from the dev Mac network, not the hub.",
                        "Same audio repeated 3x: samples are not independent."],
        "total_estimated_cost_usd": round(spent, 4), "paths": {},
    }
    for path in sorted({r["path"] for r in records}):
        rs = [r for r in records if r["path"] == path]
        ok = [r for r in rs if "error" not in r]
        mism = [r for r in ok if normalize(r["transcript"]) != normalize(r["command_text"])]
        raw_mism = [r for r in ok if _raw_norm(r["transcript"]) != _raw_norm(r["command_text"])]
        fd = [r["t_end_to_first_delta"] for r in ok if r.get("t_end_to_first_delta") is not None]
        fin = [r["t_end_to_final"] for r in ok]
        seen: dict[str, dict[str, Any]] = {}
        for r in mism:
            seen.setdefault(r["command_id"], {"command": r["command_text"], "transcript": r["transcript"]})
        out["paths"][path] = {
            "samples": len(rs), "failures": len(rs) - len(ok),
            "failure_examples": [r["error"] for r in rs if "error" in r][:5],
            "t_end_to_final_p50_s": pct(fin, 0.5), "t_end_to_final_p95_s": pct(fin, 0.95),
            "t_end_to_final_mean_s": round(statistics.fmean(fin), 4) if fin else None,
            "t_end_to_first_delta_p50_s": pct(fd, 0.5), "t_end_to_first_delta_p95_s": pct(fd, 0.95),
            "deltas_median": statistics.median(r["deltas"] for r in ok) if ok else None,
            "transcript_exact_rate": round(1 - len(mism) / len(ok), 4) if ok else None,
            "transcript_exact_rate_raw": round(1 - len(raw_mism) / len(ok), 4) if ok else None,
            "accuracy_by_category": {c: _rate([normalize(r["transcript"]) == normalize(r["command_text"])
                                               for r in ok if r["category"] == c])
                                     for c in sorted({r["category"] for r in ok})},
            "cost_per_command_usd": round(statistics.fmean(r["cost_usd"] for r in ok), 6) if ok else None,
            "stt_usage_example": next((r.get("stt_usage") for r in ok if r.get("stt_usage")), None),
            "transcription_mismatches": list(seen.values()),
        }
    return out
