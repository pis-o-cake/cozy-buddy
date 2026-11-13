"""Path A: cascade STT -> light chat model with tools (streamed) -> sentence-wise streaming TTS."""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from openai import AsyncOpenAI

from s2 import pricing
from s2.audio import duration_s, pcm_to_wav
from s2.config import RESPONSE_VOICE, Models
from s2.home import CHAT_TOOLS, SYSTEM_PROMPT, execute_tool

_SENT_END = re.compile(r"(?<=[.!?。…])\s+|(?<=[.!?。…])$|\n+")
MAX_TOOL_ROUNDS = 3


def split_sentences(buf: str, final: bool) -> tuple[list[str], str]:
    """Split completed sentences off the buffer; keep the unfinished tail unless final."""
    parts = _SENT_END.split(buf)
    if final:
        return [p.strip() for p in parts if p.strip()], ""
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


async def _tts_worker(
    client: AsyncOpenAI, model: str, queue: asyncio.Queue[str | None], rec: dict[str, Any], t0: float
) -> None:
    total_bytes = 0
    while (sentence := await queue.get()) is not None:
        async with client.audio.speech.with_streaming_response.create(
            model=model, voice=RESPONSE_VOICE, input=sentence, response_format="pcm"
        ) as resp:
            async for chunk in resp.iter_bytes(4096):
                if "t_first_audio_byte" not in rec:
                    rec["t_first_audio_byte"] = time.perf_counter() - t0
                total_bytes += len(chunk)
    rec["tts_audio_s"] = total_bytes / 2 / 24000


async def run_cascade(
    client: AsyncOpenAI, models: Models, pcm: bytes, reasoning_effort: str | None
) -> dict[str, Any]:
    """Run one utterance through the cascade; all timings are seconds since t0 (upload start)."""
    rec: dict[str, Any] = {"tool_calls": [], "final_text": ""}
    wav = pcm_to_wav(pcm)
    in_s = duration_s(pcm)

    t0 = time.perf_counter()
    stt = await client.audio.transcriptions.create(
        model=models.stt, file=("utt.wav", wav, "audio/wav"), language="ko"
    )
    rec["t_stt_done"] = time.perf_counter() - t0
    rec["transcript"] = stt.text
    stt_usage = getattr(stt, "usage", None)
    rec["stt_usage"] = stt_usage.model_dump() if stt_usage else None

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": stt.text},
    ]
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    tts_task = asyncio.create_task(_tts_worker(client, models.tts, queue, rec, t0))
    usage = {"prompt": 0, "cached": 0, "completion": 0}
    extra: dict[str, Any] = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}

    try:
        for _ in range(MAX_TOOL_ROUNDS + 1):
            stream = await client.chat.completions.create(
                model=models.llm, messages=messages, tools=CHAT_TOOLS, stream=True,
                stream_options={"include_usage": True}, **extra,
            )
            text, calls, buf = "", {}, ""
            async for chunk in stream:
                if chunk.usage:
                    usage["prompt"] += chunk.usage.prompt_tokens
                    usage["completion"] += chunk.usage.completion_tokens
                    det = chunk.usage.prompt_tokens_details
                    usage["cached"] += (det.cached_tokens or 0) if det else 0
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                    rec.setdefault("t_first_llm_token", time.perf_counter() - t0)
                    rec.setdefault("t_tool_call", time.perf_counter() - t0)
                    for tc in delta.tool_calls:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        slot["id"] += tc.id or ""
                        if tc.function:
                            slot["name"] += tc.function.name or ""
                            slot["arguments"] += tc.function.arguments or ""
                if delta.content:
                    rec.setdefault("t_first_llm_token", time.perf_counter() - t0)
                    text += delta.content
                    buf += delta.content
                    if not calls:
                        sents, buf = split_sentences(buf, final=False)
                        for s in sents:
                            queue.put_nowait(s)
            if not calls:
                for s in split_sentences(buf, final=True)[0]:
                    queue.put_nowait(s)
                rec["final_text"] = text
                break
            messages.append({
                "role": "assistant", "content": text or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls.values()
                ],
            })
            for c in calls.values():
                try:
                    args = json.loads(c["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                rec["tool_calls"].append({"name": c["name"], "args": args})
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": execute_tool(c["name"], c["arguments"] or "{}")})
        else:
            rec["error"] = "max_tool_rounds"
    finally:
        queue.put_nowait(None)
        await tts_task
    rec["t_done"] = time.perf_counter() - t0

    rec["usage"] = {
        "stt_audio_s": in_s, "llm": usage, "tts_audio_s": rec.get("tts_audio_s", 0.0),
    }
    rec["cost_usd"] = (
        pricing.stt_cost(models.stt, in_s)
        + pricing.llm_cost(models.llm, usage["prompt"], usage["cached"], usage["completion"])
        + pricing.tts_cost(rec.get("tts_audio_s", 0.0))
    )
    rec["t_end_to_first_audio"] = rec.get("t_first_audio_byte")
    rec["stt_time"] = rec["t_stt_done"]
    return rec
