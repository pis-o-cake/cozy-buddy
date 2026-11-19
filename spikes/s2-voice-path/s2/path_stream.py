"""Streaming STT paths: real-time paced upload over a Realtime transcription session, plus a batch baseline.

Timing convention (shared by every path here): t_end is the moment the speaker finishes talking.
  - stream*: audio is sent in 20 ms chunks paced at wall-clock speed; t_end is when the last chunk was sent.
  - batch*:  the whole file is uploaded only after speech ends, so t_end is the instant before the upload starts.
Connection setup (WebSocket handshake + session.update) happens before speech starts and is excluded,
matching a hub that keeps a session open from the wake word onward.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any

import websockets
from openai import AsyncOpenAI

from s2 import pricing
from s2.audio import duration_s, pcm_to_wav
from s2.config import SAMPLE_RATE

WS_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * CHUNK_MS // 1000 * 2
EVENT_TIMEOUT_S = 20

KEYWORDS = ["거실", "침실", "주방", "조명", "불", "스탠드", "가습기", "전기포트", "타이머", "알람"]


async def run_stream(pcm: bytes, model: str, prompt: str | None, keywords: list[str] | None,
                     delay: str | None) -> dict[str, Any]:
    """Stream one utterance in real time; returns timings relative to t_end (last chunk sent)."""
    transcription: dict[str, Any] = {"model": model, "languages": ["ko"]}
    if prompt:
        transcription["prompt"] = prompt
    if keywords:
        transcription["keywords"] = keywords
    if delay:
        transcription["delay"] = delay
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    rec: dict[str, Any] = {"deltas": 0, "stt_model": model, "stt_prompt_used": bool(prompt or keywords),
                           "stt_delay": delay}

    async with websockets.connect(WS_URL, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "type": "transcription",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                                "transcription": transcription, "turn_detection": None}},
        }}))
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), EVENT_TIMEOUT_S))
            if ev["type"] == "session.updated":
                break
            if ev["type"] == "error":
                raise RuntimeError(f"session error: {ev.get('error')}")

        marks: dict[str, float] = {}
        text_parts: list[str] = []

        async def reader() -> None:
            async for raw in ws:
                ev = json.loads(raw)
                et = ev["type"]
                now = time.perf_counter()
                if et == "conversation.item.input_audio_transcription.delta":
                    rec["deltas"] += 1
                    marks.setdefault("first_delta", now)
                    text_parts.append(ev.get("delta", ""))
                elif et == "conversation.item.input_audio_transcription.completed":
                    marks["completed"] = now
                    rec["transcript"] = ev.get("transcript", "")
                    rec["stt_usage"] = ev.get("usage")
                    return
                elif et in ("error", "conversation.item.input_audio_transcription.failed"):
                    raise RuntimeError(f"{et}: {ev.get('error') or ev}")

        reader_task = asyncio.create_task(reader())
        start = time.perf_counter()
        n_chunks = 0
        for i in range(0, len(pcm), CHUNK_BYTES):
            wait = start + n_chunks * CHUNK_MS / 1000 - time.perf_counter()
            if wait > 0:
                await asyncio.sleep(wait)
            await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                      "audio": base64.b64encode(pcm[i:i + CHUNK_BYTES]).decode()}))
            n_chunks += 1
            if reader_task.done():
                break
        # A chunk is only "spoken" after its 20 ms elapse, so t_end is the true end of speech.
        await asyncio.sleep(max(0.0, start + n_chunks * CHUNK_MS / 1000 - time.perf_counter()))
        t_end = time.perf_counter()
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        rec["t_commit_sent"] = time.perf_counter() - t_end
        try:
            await asyncio.wait_for(reader_task, EVENT_TIMEOUT_S)
        finally:
            if not reader_task.done():
                reader_task.cancel()

    rec["audio_s"] = duration_s(pcm)
    rec["t_end_to_final"] = marks["completed"] - t_end
    rec["t_end_to_first_delta"] = marks["first_delta"] - t_end if "first_delta" in marks else None
    rec["t_start_to_first_delta"] = marks["first_delta"] - start if "first_delta" in marks else None
    rec["delta_text"] = "".join(text_parts)
    # The API reports billed duration in whole seconds (e.g. 2.7 s audio -> 3 s); prefer it over raw length.
    usage = rec.get("stt_usage") or {}
    rec["cost_usd"] = pricing.stt_cost(model, usage.get("seconds") or rec["audio_s"])
    return rec


async def run_batch(client: AsyncOpenAI, pcm: bytes, model: str, prompt: str | None) -> dict[str, Any]:
    """File-upload STT started after speech ends; t_end is the instant before the upload begins."""
    wav = pcm_to_wav(pcm)
    kwargs: dict[str, Any] = {"prompt": prompt} if prompt else {}
    t_end = time.perf_counter()
    res = await client.audio.transcriptions.create(model=model, file=("utt.wav", wav, "audio/wav"),
                                                   language="ko", **kwargs)
    return {"t_end_to_final": time.perf_counter() - t_end, "t_end_to_first_delta": None, "transcript": res.text,
            "stt_model": model, "stt_prompt_used": bool(prompt), "audio_s": duration_s(pcm), "deltas": 0,
            "cost_usd": pricing.stt_cost(model, duration_s(pcm))}
