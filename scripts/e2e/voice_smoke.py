"""가상 허브 종단 간 스모크 도구.

실제 허브처럼 개발 서버 WebSocket 에 붙어 음성 루프(STT -> 매처 -> LLM -> TTS)를 검증한다.

정리(cleanup) 방식:
  - 조명 등 기기 상태: 시작 시 HomeSnapshot 을 기억하고, 종료 시 바뀐 기기를 EntityCommand
    (turn_on/turn_off, 밝기)로 원래 상태로 되돌린다. --no-restore 로 끈다.
  - 알람/타이머: 서버가 허브로 보낸 AlarmUpsert 에 AlarmAck(ok) 만 답한다. 만든 알람은 시나리오의
    뒤따르는 "취소" 발화로 지운다(허브는 알람을 직접 삭제하지 않는다). 시나리오를 중간에 멈추면
    남을 수 있으니 종료 시 남은 알람 ID 를 경고로 출력한다.
  - 비밀값: 저장소 루트 .env 를 프로세스 안에서만 읽고 출력하지 않는다.

사용 예:
  uv run python voice_smoke.py --scenario scenarios/basic.yaml --repeat 3 --json out/r.json
  uv run python voice_smoke.py --utterance "거실 불 꺼줘" --expect-state light.living=off
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import yaml
from dotenv import load_dotenv
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROTO_DIR = REPO / "protocol" / "proto"
DEFAULT_ENV = Path("/Users/cheol/dev/private/cozy-buddy/.env")
CACHE_DIR = HERE / ".cache"
TTS_RATE = 24000
SEND_RATE = 16000
CHUNK_BYTES = SEND_RATE * 2 * 20 // 1000
TAIL_SILENCE_MS = 300
HUB_ID = "hub-dev-01"
TURN_TIMEOUT_S = 45.0
# gpt-4o-mini-tts 분당 추정가(S2 스파이크와 같은 가정)
TTS_USD_PER_MIN = 0.015


def generate_protobuf() -> Path:
    """protoc 로 파이썬 코드를 임시 디렉터리에 생성해 sys.path 에 올린다."""
    from grpc_tools import protoc

    out = Path(tempfile.mkdtemp(prefix="cozy-gen-"))
    protos = sorted(str(p) for p in (PROTO_DIR / "cozy" / "v1").glob("*.proto"))
    code = protoc.main(["protoc", f"-I{PROTO_DIR}", f"--python_out={out}", *protos])
    if code != 0:
        raise RuntimeError("protoc failed")
    sys.path.insert(0, str(out))
    return out


generate_protobuf()
from cozy.v1 import alarm_pb2, home_pb2, session_pb2, voice_pb2  # noqa: E402
from cozy.v1.envelope_pb2 import Envelope  # noqa: E402
from websockets.asyncio.client import ClientConnection, connect  # noqa: E402

STATE_NAMES = {v: k.removeprefix("STATE_") for k, v in voice_pb2.AssistantState.State.items()}


@dataclass
class Result:
    """발화 하나의 측정 결과."""

    text: str
    repeat: int = 0
    transcript: str = ""
    response_text: str = ""
    states: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    transcript_ms: int | None = None
    first_text_ms: int | None = None
    first_audio_ms: int | None = None
    audio_end_ms: int | None = None
    done_ms: int | None = None
    audio_chunks: int = 0
    audio_seconds: float = 0.0
    audio_problems: list[str] = field(default_factory=list)
    alarm_upserts: list[str] = field(default_factory=list)
    changed_entities: dict[str, str] = field(default_factory=dict)
    expect: str = ""
    expect_ok: bool | None = None
    tts_cached: bool = False

    @property
    def failed(self) -> bool:
        return bool(
            self.errors or self.audio_chunks == 0 or self.audio_problems or self.expect_ok is False
        )


def load_env(path: Path) -> tuple[str, str, str]:
    """(허브 토큰, OpenAI 키, TTS 모델). 값은 출력하지 않는다."""
    load_dotenv(path)
    token = ""
    for item in os.environ.get("COZY_STATIC_TOKENS", "").split(","):
        parts = item.strip().split(":", 2)
        if len(parts) == 3 and parts[0] == "hub" and parts[1] == HUB_ID:
            token = parts[2]
    if not token:
        raise SystemExit(f"hub token for {HUB_ID} not found in COZY_STATIC_TOKENS")
    model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    return token, os.environ.get("OPENAI_API_KEY", ""), model


def to_16k(pcm24: bytes) -> bytes:
    samples = np.frombuffer(pcm24, dtype="<i2").astype(np.float32)
    out = resample_poly(samples, SEND_RATE, TTS_RATE)
    return np.clip(out, -32768, 32767).astype("<i2").tobytes()


class Synth:
    """OpenAI TTS 합성. 텍스트 해시로 .cache 에 캐시한다."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key, self.model = api_key, model
        self.spent_chars = 0
        self.spent_seconds = 0.0

    def synth(self, text: str) -> tuple[bytes, bool]:
        CACHE_DIR.mkdir(exist_ok=True)
        key = hashlib.sha256(f"{self.model}|{text}".encode()).hexdigest()[:24]
        path = CACHE_DIR / f"{key}.pcm"
        if path.exists():
            return path.read_bytes(), True
        if not self.api_key:
            raise SystemExit("OPENAI_API_KEY missing for TTS synthesis")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, max_retries=2, timeout=60)
        with client.audio.speech.with_streaming_response.create(
            model=self.model,
            voice=os.environ.get("OPENAI_TTS_VOICE", "echo"),
            input=text,
            response_format="pcm",
        ) as response:
            pcm24 = b"".join(response.iter_bytes(4096))
        pcm16 = to_16k(pcm24) + b"\x00" * (SEND_RATE * 2 * TAIL_SILENCE_MS // 1000)
        path.write_bytes(pcm16)
        self.spent_chars += len(text)
        self.spent_seconds += len(pcm24) / 2 / TTS_RATE
        return pcm16, False


def read_wav16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise SystemExit("wav must be mono PCM16")
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())
    if rate != SEND_RATE:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        out = resample_poly(samples, SEND_RATE, rate)
        pcm = np.clip(out, -32768, 32767).astype("<i2").tobytes()
    return pcm + b"\x00" * (SEND_RATE * 2 * TAIL_SILENCE_MS // 1000)


def _clone(ent: home_pb2.Entity) -> home_pb2.Entity:
    copy = home_pb2.Entity()
    copy.CopyFrom(ent)
    return copy


class VirtualHub:
    """허브 한 대를 흉내 내는 WebSocket 클라이언트."""

    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url, token
        self.ws: ClientConnection
        self.seq = 0
        self.entities: dict[str, home_pb2.Entity] = {}
        self.areas: dict[str, str] = {}
        self.initial: dict[str, home_pb2.Entity] = {}
        self.live_alarm_ids: set[str] = set()
        self.server_version = ""
        self.audio: list[bytes] = []

    async def send(self, **body: object) -> None:
        self.seq += 1
        await self.ws.send(Envelope(seq=self.seq, **body).SerializeToString())

    async def recv(self, timeout: float) -> Envelope:
        env = Envelope()
        async with asyncio.timeout(timeout):
            env.ParseFromString(await self.ws.recv(decode=False))
        return env

    async def connect(self) -> None:
        self.ws = await connect(self.url, max_size=None)
        hello = session_pb2.ClientHello(
            kind=session_pb2.CLIENT_KIND_HUB,
            client_id=HUB_ID,
            token=self.token,
            app_version="e2e-smoke",
            protocol_version=1,
        )
        await self.send(client_hello=hello)
        got_snapshot = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                env = await self.recv(1.5 if got_snapshot else 5)
            except TimeoutError:
                if got_snapshot:
                    break
                continue
            self.track(env)
            body = env.WhichOneof("body")
            if body == "error":
                raise SystemExit(f"handshake error: {env.error.message_key}")
            if body == "server_hello":
                self.server_version = env.server_hello.server_version
            if body == "home_snapshot":
                got_snapshot = True
            if body == "alarm_sync_request":
                await self.send(alarm_snapshot=alarm_pb2.AlarmSnapshot(hub_id=HUB_ID))
        if not got_snapshot:
            raise SystemExit("HomeSnapshot not received")
        self.initial = {k: _clone(v) for k, v in self.entities.items()}

    def track(self, env: Envelope) -> None:
        body = env.WhichOneof("body")
        if body == "home_snapshot":
            self.areas = {a.area_id: a.name for a in env.home_snapshot.areas}
            self.entities = {e.entity_id: e for e in env.home_snapshot.entities}
        elif body == "entity_state_changed":
            ent = self.entities.get(env.entity_state_changed.entity_id)
            if ent is not None:
                ent.state.CopyFrom(env.entity_state_changed.state)

    async def close(self) -> None:
        await self.ws.close()

    def area_lights(self, area_name: str) -> list[str]:
        ids = {i for i, n in self.areas.items() if area_name in n}
        return [
            e.entity_id for e in self.entities.values() if e.domain == "light" and e.area_id in ids
        ]

    async def run_turn(
        self, result: Result, pcm: bytes | None, text_query: str | None, realtime: bool, area_id: str
    ) -> None:
        stream_id = f"e2e-{int(time.time() * 1000)}"
        t_end = time.monotonic()
        if text_query is not None:
            query = voice_pb2.TextQuery(request_id=stream_id, text=text_query, area_id=area_id)
            await self.send(text_query=query)
        else:
            assert pcm is not None
            fmt = voice_pb2.AudioFormat(
                encoding=voice_pb2.AUDIO_ENCODING_PCM_S16LE, sample_rate_hz=SEND_RATE, channels=1
            )
            start = voice_pb2.UtteranceStart(
                stream_id=stream_id, format=fmt, area_id=area_id, source=voice_pb2.WAKE_SOURCE_TAP
            )
            await self.send(utterance_start=start)
            for index, off in enumerate(range(0, len(pcm), CHUNK_BYTES)):
                chunk = voice_pb2.AudioChunk(
                    kind=voice_pb2.STREAM_KIND_UTTERANCE_UP,
                    stream_id=stream_id,
                    index=index,
                    data=pcm[off : off + CHUNK_BYTES],
                )
                await self.send(audio_chunk=chunk)
                if realtime:
                    await asyncio.sleep(0.02)
            t_end = time.monotonic()
            await self.send(utterance_end=voice_pb2.UtteranceEnd(stream_id=stream_id))

        def ms() -> int:
            return int((time.monotonic() - t_end) * 1000)

        texts: list[str] = []
        indices: list[int] = []
        last_flags: list[bool] = []
        audio_bytes = 0
        thinking = False
        idle_at: float | None = None
        deadline = time.monotonic() + TURN_TIMEOUT_S
        while time.monotonic() < deadline:
            wait = 1.5 if idle_at is not None else deadline - time.monotonic()
            try:
                env = await self.recv(max(wait, 0.1))
            except TimeoutError:
                if idle_at is None:
                    result.errors.append("timeout")
                break
            self.track(env)
            body = env.WhichOneof("body")
            if body == "transcript":
                if result.transcript_ms is None:
                    result.transcript_ms = ms()
                result.transcript = env.transcript.text
            elif body == "response_text":
                if result.first_text_ms is None:
                    result.first_text_ms = ms()
                texts.append(env.response_text.delta)
            elif body == "audio_chunk":
                chunk = env.audio_chunk
                if chunk.kind != voice_pb2.STREAM_KIND_RESPONSE_DOWN:
                    result.audio_problems.append(f"kind={chunk.kind}")
                if result.first_audio_ms is None:
                    result.first_audio_ms = ms()
                indices.append(chunk.index)
                last_flags.append(chunk.last)
                audio_bytes += len(chunk.data)
                self.audio.append(chunk.data)
            elif body == "response_audio_end":
                result.audio_end_ms = ms()
            elif body == "assistant_state":
                name = STATE_NAMES.get(env.assistant_state.state, "?")
                result.states.append(name)
                thinking = thinking or name == "THINKING"
                if name == "IDLE" and thinking:
                    result.done_ms = ms()
                    idle_at = time.monotonic()
            elif body == "entity_state_changed":
                changed = env.entity_state_changed
                result.changed_entities[changed.entity_id] = changed.state.state
            elif body == "alarm_upsert":
                up = env.alarm_upsert
                label = up.alarm.label or up.alarm.alarm_id[:6]
                result.alarm_upserts.append(f"kind{up.alarm.kind}:{label}")
                self.live_alarm_ids.add(up.alarm.alarm_id)
                ack = alarm_pb2.AlarmAck(
                    request_id=up.request_id,
                    alarm_id=up.alarm.alarm_id,
                    revision=up.alarm.revision,
                    ok=True,
                )
                await self.send(alarm_ack=ack)
            elif body == "alarm_delete":
                dl = env.alarm_delete
                self.live_alarm_ids.discard(dl.alarm_id)
                ack = alarm_pb2.AlarmAck(request_id=dl.request_id, alarm_id=dl.alarm_id, ok=True)
                await self.send(alarm_ack=ack)
            elif body == "alarm_sync_request":
                await self.send(alarm_snapshot=alarm_pb2.AlarmSnapshot(hub_id=HUB_ID))
            elif body == "ping":
                await self.send(pong=session_pb2.Pong(ping_sent_epoch_ms=env.ping.sent_epoch_ms))
            elif body == "error":
                result.errors.append(env.error.message_key or "error")
                if idle_at is None:
                    idle_at = time.monotonic()
        result.response_text = "".join(texts)
        result.audio_chunks = len(indices)
        result.audio_seconds = round(audio_bytes / 2 / TTS_RATE, 2)
        if indices and indices != list(range(len(indices))):
            result.audio_problems.append("index gap")
        if indices and not last_flags[-1]:
            result.audio_problems.append("no last flag")
        if any(last_flags[:-1]):
            result.audio_problems.append("early last")

    async def restore(self) -> list[str]:
        notes: list[str] = []
        for eid, before in self.initial.items():
            now = self.entities.get(eid)
            if now is None or before.domain not in ("light", "switch"):
                continue
            if now.state.state == before.state.state:
                continue
            action = "turn_on" if before.state.state == "on" else "turn_off"
            cmd = home_pb2.EntityCommand(request_id=f"restore-{eid}", entity_id=eid, action=action)
            await self.send(entity_command=cmd)
            bri = before.state.attributes.get("brightness", "")
            if action == "turn_on" and bri.isdecimal():
                pct_value = max(1, round(int(bri) * 100 / 255))
                cmd = home_pb2.EntityCommand(
                    request_id=f"restore-b-{eid}",
                    entity_id=eid,
                    action="set_brightness",
                    params={"brightness_pct": str(pct_value)},
                )
                await self.send(entity_command=cmd)
            notes.append(f"restored {eid} -> {before.state.state}")
        if notes:
            # 센서 갱신이 계속 올 수 있으므로 시간 상한을 둔다.
            end = time.monotonic() + 3.0
            while time.monotonic() < end:
                try:
                    self.track(await self.recv(0.5))
                except TimeoutError:
                    break
        return notes


def pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def save_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TTS_RATE)
        w.writeframes(pcm)


def parse_expect(spec: str, hub: VirtualHub) -> list[tuple[str, str]]:
    """'entity_id=on' 또는 '거실=on'(방 조명 중 하나) 을 (entity_id, state) 후보 목록으로 푼다."""
    key, _, state = spec.partition("=")
    if key in hub.entities:
        return [(key, state)]
    return [(eid, state) for eid in hub.area_lights(key)]


def fmt(v: int | None) -> str:
    return "-" if v is None else str(v)


def print_row(r: Result) -> None:
    flag = "FAIL" if r.failed else "ok"
    print(
        f"[{flag}] #{r.repeat} {r.text!r}\n"
        f"    transcript={r.transcript!r}\n    response={r.response_text!r}\n"
        f"    ms: transcript={fmt(r.transcript_ms)} text={fmt(r.first_text_ms)} "
        f"audio={fmt(r.first_audio_ms)} audio_end={fmt(r.audio_end_ms)} done={fmt(r.done_ms)} "
        f"chunks={r.audio_chunks} ({r.audio_seconds}s)\n"
        f"    states={'>'.join(r.states)} alarms={r.alarm_upserts} changed={r.changed_entities} "
        f"expect={r.expect or '-'}:{r.expect_ok} errors={r.errors} problems={r.audio_problems}"
    )


def print_summary(results: list[Result], synth: Synth, notes: list[str]) -> None:
    print("\nutterance | n | p50 transcript | p50 text | p50 audio | p50 audio_end | fail")
    seen = list(dict.fromkeys(r.text for r in results))
    for text in seen:
        rs = [r for r in results if r.text == text]

        def col(attr: str) -> str:
            vals = [getattr(r, attr) for r in rs if getattr(r, attr) is not None]
            return fmt(pct(vals, 0.5))

        print(
            f"{text} | {len(rs)} | {col('transcript_ms')} | {col('first_text_ms')} | "
            f"{col('first_audio_ms')} | {col('audio_end_ms')} | {sum(r.failed for r in rs)}"
        )
    for n in notes:
        print(n)
    usd = synth.spent_seconds / 60 * TTS_USD_PER_MIN
    print(f"TTS billed: {synth.spent_chars} chars, {synth.spent_seconds:.1f}s audio, ~${usd:.4f}")
    all_ms = [r.first_audio_ms for r in results if r.first_audio_ms is not None]
    if all_ms:
        print(f"first_audio p50={statistics.median(all_ms):.0f}ms p95={pct(all_ms, 0.95)}ms")


async def amain(args: argparse.Namespace) -> int:
    token, api_key, tts_model = load_env(Path(args.env))
    items: list[dict[str, str]] = [{"text": t} for t in args.utterance or []]
    if args.scenario:
        loaded = yaml.safe_load(Path(args.scenario).read_text(encoding="utf-8"))["utterances"]
        items = loaded + items
    if args.expect_state and items:
        items[-1] = {**items[-1], "expect_state": args.expect_state}
    synth = Synth(api_key, tts_model)
    hub = VirtualHub(args.url, token)
    await hub.connect()
    print(
        f"connected hub={HUB_ID} server={hub.server_version} "
        f"entities={len(hub.entities)} areas={len(hub.areas)}"
    )
    if args.dump_home:
        for e in hub.entities.values():
            area = hub.areas.get(e.area_id, "-")
            print(f"  {e.entity_id:35} {area:8} {e.name!r:20} {e.state.state}")

    jobs: list[tuple[dict[str, str], bytes | None, bool]] = []
    for item in items:
        pcm, cached = synth.synth(item["text"])
        jobs.append((item, pcm, cached))
    if args.wav:
        jobs.append(({"text": f"wav:{Path(args.wav).name}"}, read_wav16k(Path(args.wav)), True))
    if args.text_query:
        jobs.append(({"text": args.text_query, "text_query": "1"}, None, True))

    results: list[Result] = []
    save_dir = Path(args.save_audio) if args.save_audio else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
    for rep in range(args.repeat):
        for item, pcm, cached in jobs:
            r = Result(text=item["text"], repeat=rep, tts_cached=cached)
            hub.audio = []
            spec = item.get("expect_state") or item.get("expect_area_light", "")
            r.expect = spec
            query = item["text"] if "text_query" in item else None
            await hub.run_turn(r, pcm, query, args.realtime, args.area_id)
            if spec:
                candidates = parse_expect(spec, hub)
                r.expect_ok = any(r.changed_entities.get(e) == s for e, s in candidates)
            if save_dir and hub.audio:
                save_wav(save_dir / f"{len(results):02d}.wav", b"".join(hub.audio))
            results.append(r)
            print_row(r)
            await asyncio.sleep(args.gap)

    notes = [] if args.no_restore else await hub.restore()
    if hub.live_alarm_ids:
        print(f"WARNING: alarms possibly left on server: {sorted(hub.live_alarm_ids)}")
    await hub.close()
    print_summary(results, synth, notes)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "results": [asdict(r) for r in results],
            "restore": notes,
            "tts_seconds_billed": round(synth.spent_seconds, 1),
            "tts_usd_estimate": round(synth.spent_seconds / 60 * TTS_USD_PER_MIN, 4),
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if any(r.failed for r in results) else 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--url", default="ws://192.168.0.171:8120/ws")
    p.add_argument("--utterance", action="append")
    p.add_argument("--scenario")
    p.add_argument("--wav")
    p.add_argument("--text-query")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--json")
    p.add_argument("--save-audio")
    p.add_argument("--expect-state", help="entity_id=on|off (마지막 발화에 적용)")
    p.add_argument("--realtime", action="store_true", help="오디오를 20ms 간격으로 전송")
    p.add_argument("--no-restore", action="store_true")
    p.add_argument("--dump-home", action="store_true")
    p.add_argument("--area-id", default="")
    p.add_argument("--gap", type=float, default=1.0, help="발화 사이 대기 초")
    p.add_argument("--env", default=str(DEFAULT_ENV))
    args = p.parse_args()
    if not (args.utterance or args.scenario or args.wav or args.text_query or args.dump_home):
        p.error("need --utterance, --scenario, --wav, --text-query or --dump-home")
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
