"""허브 간 방송: 대상 허브를 고르고 한 번 합성한 음성을 각 허브에 내려보낸다."""

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import (
    AUDIO_ENCODING_PCM_S16LE,
    STREAM_KIND_BROADCAST_DOWN,
    AudioChunk,
    AudioFormat,
    BroadcastNotice,
    ResponseAudioEnd,
    ResponseAudioStart,
)
from loguru import logger

from app.core.connections import ConnectionManager
from app.core.database import Database
from app.core.i18n import Translator
from app.core.openai_gateway import TTS_SAMPLE_RATE_HZ, OpenAIGateway, OpenAIGatewayError
from app.domain.agent.model import AreaRef
from app.domain.agent.normalize import compact_form
from app.domain.hub import crud as hub_crud
from app.domain.hub.models import Hub
from app.domain.voice.audio import PCM_CHANNELS, RESPONSE_CHUNK_MS, PcmChunker
from app.domain.voice.tts_text import normalize_for_tts

ALL_TARGET = "all"
MAX_TEXT_CHARS = 200
KEY_TARGET_ALL = "broadcast.target_all"
KEY_SPOKEN = "broadcast.spoken"
KEY_SPOKEN_FROM = "broadcast.spoken_from"
STREAM_PREFIX = "bc-"


@dataclass(frozen=True, slots=True)
class BroadcastRequest:
    """방송 요청 하나.

    Attributes:
        target: 방·허브 이름 또는 ALL_TARGET. None 도 전체로 본다.
        text: 방송 문장. 자막과 음성의 재료다.
        from_hub_id: 말한 허브. 대상에서 빼고 알림에 싣는다. 앱 요청이면 빈 문자열.
        from_area_id: 말한 허브가 놓인 방 ID.
        from_area_name: 말한 허브가 놓인 방 이름. 있으면 음성 앞머리에서 밝힌다.
        areas: 대상 이름을 방 ID 로 풀 때 쓰는 집의 방 목록.
    """

    target: str | None
    text: str
    from_hub_id: str = ""
    from_area_id: str = ""
    from_area_name: str = ""
    areas: Sequence[AreaRef] = ()


@dataclass(frozen=True, slots=True)
class BroadcastReport:
    """방송 결과.

    Attributes:
        label: 사용자에게 읽어 줄 대상 이름.
        everyone: 말한 허브를 뺀 전체가 대상이었는지.
        delivered: 알림을 보낸 허브.
        offline: 대상이지만 연결돼 있지 않거나 전송에 실패한 허브.
        audio: 음성을 내려보냈는지. 합성할 수 없으면 자막만 갔다.
    """

    label: str
    everyone: bool
    delivered: tuple[str, ...] = ()
    offline: tuple[str, ...] = ()
    audio: bool = False

    @property
    def matched(self) -> int:
        """대상으로 고른 허브 수."""
        return len(self.delivered) + len(self.offline)


@dataclass(slots=True)
class _Stream:
    """진행 중인 방송 오디오 스트림 하나."""

    stream_id: str
    hubs: list[str]
    chunker: PcmChunker = field(
        default_factory=lambda: PcmChunker(TTS_SAMPLE_RATE_HZ, RESPONSE_CHUNK_MS)
    )
    index: int = 0
    started: bool = False


class BroadcastService:
    """앱 수명 동안 하나만 존재한다. 에이전트 스킬과 REST 가 같이 쓴다."""

    def __init__(
        self,
        translate: Translator,
        connections: ConnectionManager,
        database: Callable[[], Database],
        openai: OpenAIGateway | None,
    ) -> None:
        self._t = translate
        self._connections = connections
        self._database = database
        self._openai = openai

    def target_label(self, target: str | None) -> str:
        """대상 이름을 사용자에게 읽어 줄 문구로 만든다."""
        return self._t(KEY_TARGET_ALL) if _is_everyone(target) else str(target)

    async def send(self, request: BroadcastRequest) -> BroadcastReport:
        """대상 허브에 방송 알림과 음성을 보낸다.

        IMPORTANT: 방송 문장은 사용자가 정한 자유 문장이라 TTS 캐시를 쓰지 않는다.
        음성은 한 번만 합성하고 같은 PCM 조각을 대상 허브 모두에게 보낸다.

        Args:
            request: 방송 요청.

        Returns:
            대상 허브별 전달 결과. 대상을 하나도 찾지 못하면 matched 가 0 이다.
        """
        everyone = _is_everyone(request.target)
        label = self.target_label(request.target)
        targets = await self._select_targets(request)
        online = self._connections.online_hub_ids()
        live = [hub_id for hub_id in targets if hub_id in online]
        offline = [hub_id for hub_id in targets if hub_id not in online]
        if not live:
            return BroadcastReport(label, everyone, (), tuple(offline))
        stream_id = f"{STREAM_PREFIX}{uuid.uuid4().hex[:12]}"
        spoken = self._spoken_text(request)
        notice = BroadcastNotice(
            from_hub_id=request.from_hub_id,
            from_area=request.from_area_name or request.from_area_id,
            text=request.text,
            stream_id=stream_id,
        )
        reached = await self._push(live, Envelope(broadcast_notice=notice))
        offline.extend(hub_id for hub_id in live if hub_id not in reached)
        audio = False
        if reached:
            audio = await self._stream_audio(_Stream(stream_id, list(reached)), spoken)
        logger.info(
            "Broadcast sent: from={} target={} delivered={} offline={} audio={}",
            request.from_hub_id or "-",
            label,
            len(reached),
            len(offline),
            audio,
        )
        return BroadcastReport(label, everyone, tuple(reached), tuple(offline), audio)

    def _spoken_text(self, request: BroadcastRequest) -> str:
        if request.from_area_name:
            template = self._t(KEY_SPOKEN_FROM)
            return template.format(area=request.from_area_name, text=request.text)
        return self._t(KEY_SPOKEN).format(text=request.text)

    async def _select_targets(self, request: BroadcastRequest) -> list[str]:
        async with self._database().session() as session:
            hubs = list(await hub_crud.list_all(session))
        others = [hub for hub in hubs if hub.hub_id != request.from_hub_id]
        if _is_everyone(request.target):
            return [hub.hub_id for hub in others]
        wanted = compact_form(str(request.target))
        area_ids = {
            area.area_id
            for area in request.areas
            if wanted in {compact_form(n) for n in (area.name, *area.aliases)}
        }
        return [hub.hub_id for hub in others if _hub_matches(hub, wanted, area_ids)]

    async def _push(self, hub_ids: Sequence[str], envelope: Envelope) -> list[str]:
        """허브마다 복사본을 보내고 전송에 성공한 허브를 돌려준다."""

        async def one(hub_id: str) -> bool:
            copy = Envelope()
            copy.CopyFrom(envelope)
            return await self._connections.send_to_hub(hub_id, copy)

        results = await asyncio.gather(*(one(hub_id) for hub_id in hub_ids))
        return [hub_id for hub_id, ok in zip(hub_ids, results, strict=True) if ok]

    async def _stream_audio(self, stream: _Stream, spoken: str) -> bool:
        """합성되는 대로 모든 대상 허브에 흘린다. 첫 조각 전에 실패하면 음성 없이 끝낸다."""
        gateway = self._openai
        text = normalize_for_tts(spoken)
        if gateway is None or not text:
            return False
        try:
            async for pcm in gateway.stream_speech(text):
                for chunk in stream.chunker.feed(pcm):
                    await self._send_chunk(stream, chunk, last=False)
        except OpenAIGatewayError as exc:
            logger.warning("Broadcast TTS failed: {}", exc)
        finally:
            # 취소돼도 이미 시작한 스트림은 닫아 허브 재생기가 멈추지 않게 한다.
            if stream.started:
                await self._send_chunk(stream, stream.chunker.flush(), last=True)
                end = ResponseAudioEnd(stream_id=stream.stream_id)
                await self._push(stream.hubs, Envelope(response_audio_end=end))
        return stream.started

    async def _send_chunk(self, stream: _Stream, data: bytes, *, last: bool) -> None:
        if not stream.started:
            stream.started = True
            start = ResponseAudioStart(
                stream_id=stream.stream_id,
                kind=STREAM_KIND_BROADCAST_DOWN,
                format=AudioFormat(
                    encoding=AUDIO_ENCODING_PCM_S16LE,
                    sample_rate_hz=TTS_SAMPLE_RATE_HZ,
                    channels=PCM_CHANNELS,
                ),
            )
            stream.hubs = await self._push(stream.hubs, Envelope(response_audio_start=start))
        chunk = AudioChunk(
            kind=STREAM_KIND_BROADCAST_DOWN,
            stream_id=stream.stream_id,
            index=stream.index,
            data=data,
            last=last,
        )
        stream.index += 1
        stream.hubs = await self._push(stream.hubs, Envelope(audio_chunk=chunk))


def _is_everyone(target: str | None) -> bool:
    return target is None or not target.strip() or target.strip().lower() == ALL_TARGET


def _hub_matches(hub: Hub, wanted: str, area_ids: set[str]) -> bool:
    if hub.area_id and hub.area_id in area_ids:
        return True
    names = (hub.area_id, hub.name, hub.hub_id)
    return any(name and compact_form(name) == wanted for name in names)
