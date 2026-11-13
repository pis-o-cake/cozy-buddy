"""Path B: one realtime speech-to-speech session per command, manual commit, function calling."""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from openai import AsyncOpenAI

from s2 import pricing
from s2.config import RESPONSE_VOICE, SAMPLE_RATE, Models
from s2.home import REALTIME_TOOLS, SYSTEM_PROMPT, execute_tool

CHUNK_BYTES = 4800 * 2  # 200 ms of 24 kHz PCM16
AUDIO_DELTA_TYPES = {"response.output_audio.delta", "response.audio.delta"}


def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
    for key, val in usage.items():
        if isinstance(val, dict):
            _merge_usage(total.setdefault(key, {}), val)
        elif isinstance(val, int):
            total[key] = total.get(key, 0) + val


async def run_realtime(client: AsyncOpenAI, models: Models, pcm: bytes) -> dict[str, Any]:
    """Run one utterance; t0 is the moment input_audio_buffer.commit is sent."""
    rec: dict[str, Any] = {"tool_calls": [], "final_text": ""}
    usage: dict[str, Any] = {}
    audio_bytes = 0
    transcripts: list[str] = []

    async with client.realtime.connect(model=models.realtime) as conn:
        await conn.send({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": SYSTEM_PROMPT,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "turn_detection": None,
                    },
                    "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "voice": RESPONSE_VOICE},
                },
                "tools": REALTIME_TOOLS,
                "tool_choice": "auto",
            },
        })
        for i in range(0, len(pcm), CHUNK_BYTES):
            await conn.send({"type": "input_audio_buffer.append",
                             "audio": base64.b64encode(pcm[i:i + CHUNK_BYTES]).decode()})
        t0 = time.perf_counter()
        await conn.send({"type": "input_audio_buffer.commit"})
        await conn.send({"type": "response.create"})

        async for event in conn:
            etype = event.type
            if etype == "error":
                raise RuntimeError(f"realtime error: {getattr(event, 'error', event)}")
            if etype in AUDIO_DELTA_TYPES:
                rec.setdefault("t_first_audio_byte", time.perf_counter() - t0)
                audio_bytes += len(base64.b64decode(event.delta))
            elif etype in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                transcripts.append(event.transcript)
            elif etype == "response.output_item.done" and getattr(event.item, "type", "") == "function_call":
                rec.setdefault("t_tool_call", time.perf_counter() - t0)
            elif etype == "response.done":
                resp = event.response.model_dump()
                if resp.get("status") == "failed":
                    raise RuntimeError(f"response failed: {resp.get('status_details')}")
                _merge_usage(usage, resp.get("usage") or {})
                calls = [o for o in resp.get("output", []) if o.get("type") == "function_call"]
                if not calls:
                    break
                for c in calls:
                    try:
                        args = json.loads(c.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    rec["tool_calls"].append({"name": c["name"], "args": args})
                    await conn.send({"type": "conversation.item.create", "item": {
                        "type": "function_call_output", "call_id": c["call_id"],
                        "output": execute_tool(c["name"], c.get("arguments") or "{}")}})
                await conn.send({"type": "response.create"})
        rec["t_done"] = time.perf_counter() - t0

    rec["final_text"] = " ".join(transcripts)
    rec["output_audio_s"] = audio_bytes / 2 / SAMPLE_RATE
    rec["usage"] = usage
    rec["cost_usd"] = pricing.realtime_cost(usage)
    rec["t_end_to_first_audio"] = rec.get("t_first_audio_byte")
    return rec
