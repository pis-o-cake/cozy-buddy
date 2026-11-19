"""자주 쓰는 확인 문장을 미리 합성해 TTS 캐시를 채운다.

사용자 턴이 진행 중이면 합성을 미루고, 하루 합성 문장 수 상한을 넘기면 멈춘다.
"""

import asyncio
import contextlib
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from cozy.v1.home_pb2 import HomeSnapshot
from loguru import logger

from app.config import Settings
from app.core.i18n import Translator
from app.core.openai_gateway import OpenAIGateway, OpenAIGatewayError
from app.domain.agent.home_view import build_home_view
from app.domain.agent.model import DeviceAction, DeviceCommand, HomeModel
from app.domain.agent.responses import build_response, render
from app.domain.voice.tts_cache import TtsCache
from app.domain.voice.tts_text import normalize_for_tts

WARMUP_DOMAINS = frozenset({"light", "switch"})
WARMUP_ACTIONS = (DeviceAction.TURN_ON, DeviceAction.TURN_OFF)
# 집 구성과 무관하게 자주 나오는 고정 문장의 언어팩 키.
FIXED_KEYS = (
    "agent.alarm_control.stopped",
    "agent.alarm.cancel",
    "voice.not_heard",
)
_CONCURRENCY = 2


class TtsWarmup:
    """백그라운드 웜업. 집 구성이 바뀌면 새 문장만 합성한다."""

    def __init__(
        self,
        settings: Settings,
        cache: TtsCache,
        gateway: OpenAIGateway,
        translate: Translator,
        snapshot: Callable[[], HomeSnapshot],
        today: Callable[[], date] | None = None,
    ) -> None:
        self._cache = cache
        self._gateway = gateway
        self._t = translate
        self._snapshot = snapshot
        self._max_per_day = settings.tts_warmup_max_per_day
        self._poll = settings.tts_warmup_poll_seconds
        self._tz = ZoneInfo(settings.timezone)
        self._today = today or (lambda: datetime.now(self._tz).date())
        self._budget_day = self._today()
        self._spent_today = 0
        self._task: asyncio.Task[None] | None = None
        self.synthesized_total = 0

    @property
    def spent_today(self) -> int:
        """오늘 합성한 문장 수."""
        self._roll_day()
        return self._spent_today

    def sentences(self) -> list[str]:
        """지금 집 구성에서 미리 합성할 TTS 문장. 순서를 지키고 중복을 없앤다."""
        model = build_home_view(self._snapshot()).model
        now = datetime.now(self._tz)
        texts = [self._t(key) for key in FIXED_KEYS]
        texts.extend(self._device_texts(model, now))
        spoken = (normalize_for_tts(text) for text in texts)
        return list(dict.fromkeys(s for s in spoken if s))

    def start(self) -> None:
        """웜업 루프를 띄운다."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="tts-warmup")

    async def stop(self) -> None:
        """진행 중인 합성을 취소하고 기다린다."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def run_once(self) -> int:
        """아직 캐시에 없는 문장을 예산 안에서 합성한다. 합성한 문장 수를 돌려준다."""
        pending = [s for s in self.sentences() if not self._cache.has(s)]
        if not pending:
            return 0
        done = 0
        gate = asyncio.Semaphore(_CONCURRENCY)

        async def one(text: str) -> None:
            nonlocal done
            async with gate:
                if await self._synthesize(text):
                    done += 1

        async with asyncio.TaskGroup() as group:
            for text in pending:
                group.create_task(one(text))
        if done:
            logger.info(
                "TTS warm-up synthesized: sentences={} spent_today={}", done, self._spent_today
            )
        return done

    # --- 내부 --------------------------------------------------------------------------------

    def _device_texts(self, model: HomeModel, now: datetime) -> list[str]:
        texts: list[str] = []
        for entity in model.entities:
            if entity.domain not in WARMUP_DOMAINS:
                continue
            for action in WARMUP_ACTIONS:
                response = build_response(
                    DeviceCommand((entity.entity_id,), action), model, now, self._t
                )
                if response is not None:
                    texts.append(render(response, self._t))
        return texts

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._budget_day:
            self._budget_day = today
            self._spent_today = 0

    async def _synthesize(self, text: str) -> bool:
        """예산을 확보하고 사용자 턴이 없을 때 한 문장을 합성해 캐시에 넣는다."""
        await self._cache.wait_idle()
        self._roll_day()
        if self._spent_today >= self._max_per_day or self._cache.has(text):
            return False
        # 합성 도중 실패해도 요청은 나갔으므로 예산을 먼저 쓴다.
        self._spent_today += 1
        pcm = bytearray()
        try:
            async for data in self._gateway.stream_speech(text):
                pcm.extend(data)
        except OpenAIGatewayError as exc:
            logger.warning("TTS warm-up failed: {}", exc)
            return False
        self._cache.put(text, bytes(pcm))
        self.synthesized_total += 1
        return True

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("TTS warm-up cycle failed")
            await asyncio.sleep(self._poll)
