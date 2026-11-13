"""Sequential runner, cost guard, raw.jsonl and summary.json writer."""
from __future__ import annotations

import asyncio
import json
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from s2 import pricing
from s2.audio import ROOT, load_commands, read_pcm, synthesize_all
from s2.config import INPUT_VOICE, RESPONSE_VOICE, Models
from s2.home import is_correct
from s2.normalize import normalize
from s2.path_a import run_cascade
from s2.path_b import run_realtime

SAMPLE_TIMEOUT_S = 60
PAUSE_S = 0.5


class BudgetExceeded(RuntimeError):
    pass


def _raw_norm(text: str) -> str:
    return re.sub(r"[\s.,!?~。]", "", text)


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


STT_PROMPT = (
    "스마트홈 음성 명령. 거실, 침실, 주방, 조명, 불, 스탠드, 가습기, 전기포트, 포트, 켜줘, 꺼줘, "
    "틀어줘, 밝기, 퍼센트, 타이머, 알람, 오전, 오후, 저녁, 새벽"
)


def stt_config(path: str, models: Models, mini_model: str, fast_stt: str) -> tuple[str, str | None, bool]:
    """Resolve (stt model, prompt, fast) for a path name."""
    fast = path == "fast"
    base = fast_stt if fast else path
    model = mini_model if "mini" in base else models.stt
    return model, (STT_PROMPT if base.endswith("prompt") else None), fast


async def _one(client: AsyncOpenAI, path: str, models: Models, pcm: bytes, effort: str | None,
               opts: tuple[str, str]) -> dict[str, Any]:
    if path == "b":
        coro = run_realtime(client, models, pcm)
    else:
        model, prompt, fast = stt_config(path, models, *opts)
        coro = run_cascade(client, models, pcm, effort, model, prompt, fast)
    return await asyncio.wait_for(coro, SAMPLE_TIMEOUT_S)


async def run(paths: list[str], repeat: int, budget: float, models: Models, limit: int | None,
              effort: str | None, out_root: Path,
              mini_model: str = "gpt-4o-mini-transcribe", fast_stt: str = "a_prompt") -> Path:
    client = AsyncOpenAI()
    commands = load_commands(limit)
    await synthesize_all(client, models.tts, commands)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_root / stamp
    out_dir.mkdir(parents=True)
    spent = 0.0
    records: list[dict[str, Any]] = []

    with (out_dir / "raw.jsonl").open("w", encoding="utf-8") as raw:
        try:
            for rep in range(repeat):
                for cmd in commands:
                    pcm = read_pcm(cmd["id"])
                    for path in paths:
                        rec = await _sample(client, path, models, pcm, effort, (mini_model, fast_stt))
                        rec.update(path=path, command_id=cmd["id"], category=cmd["category"],
                                   command_text=cmd["text"], expected=cmd["expect"], repeat=rep,
                                   models=models.as_dict())
                        rec["correct"] = "error" not in rec and is_correct(cmd["expect"], rec["tool_calls"])
                        spent += rec.get("cost_usd", 0.0)
                        records.append(rec)
                        raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        raw.flush()
                        logger.info("{} {} rep={} correct={} first_audio={} spent=${:.3f}", path, cmd["id"], rep,
                                    rec["correct"], _fmt(rec.get("t_end_to_first_audio")), spent)
                        if spent > budget:
                            raise BudgetExceeded(f"estimated spend ${spent:.2f} exceeds budget ${budget:.2f}")
                        await asyncio.sleep(PAUSE_S)
        except BudgetExceeded as exc:
            logger.error("Aborting: {}", exc)

    summary = summarize(records, models, spent, effort, mini_model, fast_stt)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print_table(summary)
    return out_dir


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}s"


async def _sample(client: AsyncOpenAI, path: str, models: Models, pcm: bytes, effort: str | None,
                  opts: tuple[str, str]) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            rec = await _one(client, path, models, pcm, effort, opts)
            rec["attempts"] = attempt + 1
            return rec
        except Exception as exc:  # noqa: BLE001 - transient errors are recorded, not fatal
            last_exc = exc
            logger.warning("Path {} attempt {} failed: {!r}", path, attempt + 1, exc)
            await asyncio.sleep(1.0)
    return {"error": repr(last_exc), "attempts": 2, "tool_calls": [], "cost_usd": 0.0}


def summarize(records: list[dict[str, Any]], models: Models, spent: float, effort: str | None,
              mini_model: str = "gpt-4o-mini-transcribe", fast_stt: str = "a_prompt") -> dict[str, Any]:
    out: dict[str, Any] = {
        "models": {**models.as_dict(), "stt_mini": mini_model, "llm_reasoning_effort": effort},
        "stt_prompt": STT_PROMPT, "fast_path_stt_variant": fast_stt,
        "normaliser": "s2/normalize.py (punctuation, spaces, sino-korean/digit numbers)",
        "voices": {"input_synthetic": INPUT_VOICE, "response": RESPONSE_VOICE},
        "pricing": {"url": pricing.PRICING_URL, "date": pricing.PRICING_DATE,
                    "stt_per_min": pricing.STT_PER_MIN, "stt_prompt_tokens_cost": "ignored (negligible)", "llm_per_1m": pricing.LLM_PRICES,
                    "realtime_per_1m": pricing.RT_PRICES,
                    "tts_per_min_assumed": pricing.TTS_PER_MIN},
        "limitations": ["Input audio is synthetic TTS speech, not real human voice.",
                        "Latency measured from the machine/network running the harness, not the hub."],
        "total_estimated_cost_usd": round(spent, 4),
        "paths": {},
    }
    for path in sorted({r["path"] for r in records}):
        rs = [r for r in records if r["path"] == path]
        ok = [r for r in rs if "error" not in r]
        lat = [r["t_end_to_first_audio"] for r in ok if r.get("t_end_to_first_audio") is not None]
        tool_rs = [r for r in ok if r["expected"]]
        chat_rs = [r for r in ok if not r["expected"]]
        entry: dict[str, Any] = {
            "samples": len(rs), "failures": len(rs) - len(ok),
            "failure_examples": [{"command": r["command_text"], "error": r["error"]} for r in rs if "error" in r][:5],
            "first_audio_p50_s": pct(lat, 0.5), "first_audio_p95_s": pct(lat, 0.95),
            "done_p50_s": pct([r["t_done"] for r in ok], 0.5),
            "accuracy_all": _rate([r["correct"] for r in rs]),
            "accuracy_tool_commands": _rate([r["correct"] for r in tool_rs]),
            "accuracy_chitchat_no_tool": _rate([r["correct"] for r in chat_rs]),
            "accuracy_by_category": {
                cat: _rate([r["correct"] for r in rs if r["category"] == cat])
                for cat in sorted({r["category"] for r in rs})
            },
            "cost_per_command_usd": round(statistics.fmean(r.get("cost_usd", 0.0) for r in ok), 5) if ok else None,
            "wrong_examples": [
                {"command": r["command_text"], "expected": r["expected"], "actual": r["tool_calls"],
                 "transcript": r.get("transcript")}
                for r in ok if not r["correct"]
            ][:12],
        }
        if path != "b":
            first = ok[0] if ok else {}
            entry["stt_model"], entry["stt_prompt_used"] = first.get("stt_model"), first.get("stt_prompt_used")
            stt = [r["stt_time"] for r in ok]
            entry["stt_p50_s"], entry["stt_p95_s"] = pct(stt, 0.5), pct(stt, 0.95)
            entry["llm_first_token_p50_s"] = pct([r["t_first_llm_token"] - r["t_stt_done"] for r in ok
                                                   if "t_first_llm_token" in r], 0.5)
            raw_mism = [r for r in ok if _raw_norm(r["transcript"]) != _raw_norm(r["command_text"])]
            mism = [r for r in ok if normalize(r["transcript"]) != normalize(r["command_text"])]
            entry["transcript_exact_rate_raw"] = round(1 - len(raw_mism) / len(ok), 4) if ok else None
            entry["transcript_exact_rate"] = round(1 - len(mism) / len(ok), 4) if ok else None
            entry["misrecognition_rate"] = round(len(mism) / len(ok), 4) if ok else None
            seen: dict[str, dict[str, Any]] = {}
            for r in mism:
                seen.setdefault(r["command_id"], {"command": r["command_text"], "transcript": r["transcript"],
                                                  "tool_correct": r["correct"]})
            entry["transcription_mismatches"] = list(seen.values())
        if path == "fast":
            matched = [r for r in ok if r.get("fast_matched")]
            fb = [r for r in ok if not r.get("fast_matched")]
            simple = {"explicit", "implicit", "colloquial", "brightness", "alarm", "timer"}
            simple_rs = [r for r in ok if r["category"] in simple]
            fl = [r["t_end_to_first_audio"] for r in matched if r.get("t_end_to_first_audio") is not None]
            entry["fast"] = {
                "matched": len(matched), "fallback": len(fb),
                "match_rate_all": _rate([bool(r.get("fast_matched")) for r in ok]),
                "match_rate_simple_categories": _rate([bool(r.get("fast_matched")) for r in simple_rs]),
                "matched_accuracy": _rate([r["correct"] for r in matched]),
                "matched_first_audio_p50_s": pct(fl, 0.5), "matched_first_audio_p95_s": pct(fl, 0.95),
                "fallback_first_audio_p50_s": pct([r["t_end_to_first_audio"] for r in fb
                                                   if r.get("t_end_to_first_audio") is not None], 0.5),
                "matched_cost_per_command_usd": round(statistics.fmean(r["cost_usd"] for r in matched), 5)
                if matched else None,
                "false_matches": [{"command": r["command_text"], "transcript": r["transcript"],
                                   "actual": r["tool_calls"]} for r in matched if not r["correct"]][:12],
                "unmatched_simple_examples": sorted({(r["command_text"], r["transcript"]) for r in simple_rs
                                                     if not r.get("fast_matched")})[:12],
            }
        out["paths"][path] = entry
    return out


def _rate(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def print_table(summary: dict[str, Any]) -> None:
    rows = [("path", "n", "fail", "p50_first_audio", "p95_first_audio", "acc_all", "acc_tool", "acc_chat",
             "usd/cmd")]
    for path, e in summary["paths"].items():
        rows.append((path, str(e["samples"]), str(e["failures"]), _fmt(e["first_audio_p50_s"]),
                     _fmt(e["first_audio_p95_s"]), str(e["accuracy_all"]), str(e["accuracy_tool_commands"]),
                     str(e["accuracy_chitchat_no_tool"]), str(e["cost_per_command_usd"])))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    for path, a in summary["paths"].items():
        if path == "b":
            continue
        print(f"path {path} stt={a['stt_model']} prompt={a['stt_prompt_used']} p50={_fmt(a['stt_p50_s'])} "
              f"p95={_fmt(a['stt_p95_s'])} exact={a['transcript_exact_rate']} miss={a['misrecognition_rate']}")
        if "fast" in a:
            print("  fast:", {k: v for k, v in a["fast"].items() if not isinstance(v, list)})
