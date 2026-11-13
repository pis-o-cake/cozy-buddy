"""CLI: models | synth | record | run."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger

from s2.audio import ROOT, load_commands, record_command, synthesize_all
from s2.config import Models, load_env


def _build_parser() -> argparse.ArgumentParser:
    d = Models()
    p = argparse.ArgumentParser(prog="s2")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_models(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--stt-model", default=d.stt)
        sp.add_argument("--llm-model", default=d.llm)
        sp.add_argument("--tts-model", default=d.tts)
        sp.add_argument("--realtime-model", default=d.realtime)

    sub.add_parser("models", help="list candidate model ids available to the key")
    sp = sub.add_parser("synth", help="synthesize input audio cache")
    add_models(sp)
    sp.add_argument("--limit", type=int)
    sp = sub.add_parser("record", help="record a command from the microphone")
    sp.add_argument("command_id")
    sp.add_argument("--seconds", type=float, default=4.0)
    sp = sub.add_parser("run", help="run the measurement")
    add_models(sp)
    sp.add_argument("--paths", default="a,b")
    sp.add_argument("--repeat", type=int, default=3)
    sp.add_argument("--limit", type=int, help="use only the first N commands")
    sp.add_argument("--budget-usd", type=float, default=5.0)
    sp.add_argument("--reasoning-effort", default="none", help="chat model reasoning_effort, e.g. none/minimal/low")
    sp.add_argument("--out", default=str(ROOT / "results"))
    return p


def main() -> None:
    args = _build_parser().parse_args()
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    if args.cmd == "record":
        record_command(args.command_id, args.seconds)
        return
    load_env()
    if args.cmd == "models":
        from openai import OpenAI

        for m in sorted(x.id for x in OpenAI().models.list().data):
            if any(k in m for k in ("gpt", "tts", "transcribe", "whisper", "realtime")):
                logger.info(m)
        return
    models = Models(args.stt_model, args.llm_model, args.tts_model, args.realtime_model)
    if args.cmd == "synth":
        from openai import AsyncOpenAI

        asyncio.run(synthesize_all(AsyncOpenAI(), models.tts, load_commands(args.limit)))
        return
    from s2.runner import run

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    asyncio.run(run(paths, args.repeat, args.budget_usd, models, args.limit, args.reasoning_effort, Path(args.out)))
