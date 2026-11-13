"""Published prices (USD) used for cost estimates."""
from __future__ import annotations

from typing import Any

PRICING_URL = "https://developers.openai.com/api/docs/pricing"
PRICING_DATE = "2026-11-13"

PER_M = 1_000_000
STT_PER_MIN = {"gpt-4o-transcribe": 0.006, "gpt-transcribe": 0.0045, "gpt-4o-mini-transcribe": 0.003}
# TTS has no published per-minute price on the page; 0.015/min is OpenAI's long-standing
# approximate figure for gpt-4o-mini-tts and is an assumption.
TTS_PER_MIN = 0.015
# (input, cached input, output) per 1M tokens
LLM_PRICES = {"gpt-5.4-nano": (0.20, 0.02, 1.25), "gpt-5.4-mini": (0.75, 0.075, 4.50)}
# audio in/cached/out, text in/cached/out per 1M tokens
RT_PRICES = {"audio": (10.0, 0.30, 20.0), "text": (0.60, 0.06, 2.40)}


def stt_cost(model: str, seconds: float) -> float:
    return STT_PER_MIN.get(model, 0.006) * seconds / 60


def tts_cost(seconds: float) -> float:
    return TTS_PER_MIN * seconds / 60


def llm_cost(model: str, prompt: int, cached: int, completion: int) -> float:
    inp, cin, out = LLM_PRICES.get(model, LLM_PRICES["gpt-5.4-mini"])
    return ((prompt - cached) * inp + cached * cin + completion * out) / PER_M


def realtime_cost(usage: dict[str, Any]) -> float:
    """Cost from summed response.done usage (audio/text details, cached split)."""
    total = 0.0
    ind = usage.get("input_token_details") or {}
    outd = usage.get("output_token_details") or {}
    cached = ind.get("cached_tokens_details") or {}
    for mod, (pin, pcache, pout) in RT_PRICES.items():
        in_tokens = ind.get(f"{mod}_tokens", 0)
        c_tokens = cached.get(f"{mod}_tokens", 0)
        total += ((in_tokens - c_tokens) * pin + c_tokens * pcache) / PER_M
        total += outd.get(f"{mod}_tokens", 0) * pout / PER_M
    return total
